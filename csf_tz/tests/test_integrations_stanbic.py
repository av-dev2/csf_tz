import json
import os
import re
import tempfile
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from csf_tz.stanbic import sftp
from csf_tz.stanbic.doctype.stanbic_payments_initiation.xml import get_payment_part, get_xml
from csf_tz.stanbic.payments import make_payments_initiation
from csf_tz.stanbic.pgp import encrypt_pgp
from csf_tz.stanbic.xml import parse_xml
from csf_tz.tests.integration_fixtures import (
	COMPANY,
	make_payroll_entry_stub,
	make_pgp_key,
	make_salary_slip_stub,
	pgp_decrypt,
)
from csf_tz.utils.create_custom_fields import create_fields_from_json, load_json

EMPLOYEES = ["_T-Employee-00001", "_T-Employee-00002"]


def strip_creation_time(xml):
	return re.sub(r"<CreDtTm>.*?</CreDtTm>", "", xml)


def make_stanbic_setting(public_key):
	values = {
		"company": COMPANY,
		"currency": "INR",
		"sftp_user": "wasco",
		"sftp_url": "sftp.example.com",
		"port": 2222,
		"user": "apiuser",
		"private_key": "/private/files/stanbic_test_key.pem",
		"initiating_party_name": "WASCO",
		"customerid": "CUST1",
		"ordering_customer_account_number": "0123456789",
		"ordering_account_type": "CACC",
		"ordering_account_currency": "INR",
		"ordering_bank_sort_code": "150000",
		"ordering_bank_country_code": "tz",
		"charges_bearer": "DEBT",
		"pgp_public_key": public_key,
		"file_code": "WASCO",
		"enabled": 1,
	}
	name = frappe.db.get_value("Stanbic Setting", {"company": COMPANY, "currency": "INR"})
	doc = frappe.get_doc("Stanbic Setting", name) if name else frappe.new_doc("Stanbic Setting")
	doc.update(values)
	doc.save() if name else doc.insert()
	return doc


def ack_xml(message_id, status="ACTC", info="Accepted"):
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.002.001.03">
	<CstmrPmtStsRpt>
		<OrgnlGrpInfAndSts>
			<OrgnlMsgId>{message_id}</OrgnlMsgId>
			<GrpSts>{status}</GrpSts>
			<StsRsnInf><AddtlInf>{info}</AddtlInf></StsRsnInf>
		</OrgnlGrpInfAndSts>
	</CstmrPmtStsRpt>
</Document>"""


def audit_xml(message_id, statuses):
	transactions = "".join(
		f"<TxInfAndSts><OrgnlEndToEndId>{slip}</OrgnlEndToEndId><StsRsnInf>{reason}</StsRsnInf></TxInfAndSts>"
		for slip, reason in statuses
	)
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document>
	<CstmrPmtStsRpt>
		<OrgnlGrpInfAndSts><OrgnlMsgId>{message_id}</OrgnlMsgId></OrgnlGrpInfAndSts>
		<OrgnlPmtInfAndSts>{transactions}</OrgnlPmtInfAndSts>
	</CstmrPmtStsRpt>
</Document>"""


class TestStanbicPayments(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_fields_from_json(load_json("16_payroll_entry_cheque.json"))
		cls.key = make_pgp_key()
		cls.setting = make_stanbic_setting(str(cls.key.pubkey))
		cls.payroll_entry = make_payroll_entry_stub()
		cls.slips = [
			make_salary_slip_stub(cls.payroll_entry, EMPLOYEES[0], 1000.5),
			make_salary_slip_stub(cls.payroll_entry, EMPLOYEES[1], 2000),
		]
		cls.temp_dir = tempfile.mkdtemp(prefix="csf_tz_stanbic_")
		cls.path_patch = patch.object(
			sftp, "get_absolute_path", side_effect=lambda path: os.path.join(cls.temp_dir, path.strip("/"))
		)
		cls.path_patch.start()
		cls.addClassCleanup(cls.path_patch.stop)
		cls.commit_patch = patch.object(frappe.db, "commit")
		cls.commit_patch.start()
		cls.addClassCleanup(cls.commit_patch.stop)

	def make_initiation(self):
		frappe.db.set_value("Payroll Entry", self.payroll_entry.name, "cheque_number", None)
		return make_payments_initiation(self.payroll_entry.name, "INR")

	def test_encrypt_pgp_round_trip(self):
		encrypted = encrypt_pgp("<xml/>", str(self.key.pubkey))
		self.assertTrue(encrypted.startswith("-----BEGIN PGP MESSAGE-----"))
		self.assertEqual(pgp_decrypt(self.key, encrypted), "<xml/>")

	def test_make_payments_initiation_builds_xml_and_marks_payroll_entry(self):
		doc = self.make_initiation()
		self.assertTrue(doc.name.startswith(self.payroll_entry.name))
		self.assertEqual(doc.stanbic_setting, self.setting.name)
		self.assertEqual(doc.number_of_transactions, 2)
		self.assertEqual(doc.control_sum, 3000.5)
		self.assertEqual(doc.file_code, "WASCO")
		self.assertEqual(
			sorted(row.salary_slip for row in doc.stanbic_payments_info), sorted(s.name for s in self.slips)
		)
		amounts = {row.salary_slip: row.transfer_amount for row in doc.stanbic_payments_info}
		self.assertEqual(amounts[self.slips[0].name], 1000.5)
		self.assertIn("<NbOfTxs>2</NbOfTxs>", doc.xml)
		self.assertIn("<CtrlSum>3000.50</CtrlSum>", doc.xml)
		self.assertIn("<Ctry>TZ</Ctry>", doc.xml)
		self.assertIn(f"<EndToEndId>{self.slips[0].name}</EndToEndId>", doc.xml)
		self.assertEqual(pgp_decrypt(self.key, doc.encrypted_xml), doc.xml)
		cheque = frappe.db.get_value(
			"Payroll Entry", self.payroll_entry.name, ["cheque_number", "cheque_date"]
		)
		self.assertEqual(cheque, (doc.name, doc.posting_date))

		self.assertRaisesRegex(
			frappe.ValidationError,
			"already created",
			make_payments_initiation,
			self.payroll_entry.name,
			"INR",
		)

	def test_make_payments_initiation_requires_setting(self):
		frappe.db.set_value("Payroll Entry", self.payroll_entry.name, "cheque_number", None)
		with self.assertRaises(frappe.DoesNotExistError):
			make_payments_initiation(self.payroll_entry.name, "USD")
		self.assertRaisesRegex(
			frappe.ValidationError,
			"Stanbic Setting not found",
			make_payments_initiation,
			self.payroll_entry.name,
			None,
		)

	def test_draft_salary_slip_blocks_initiation(self):
		draft = make_salary_slip_stub(self.payroll_entry, EMPLOYEES[0], 10, docstatus=0)
		try:
			self.assertRaisesRegex(frappe.ValidationError, "not submitted", self.make_initiation)
		finally:
			frappe.delete_doc("Salary Slip", draft.name, force=True)

	def test_payment_part_defaults_country_codes(self):
		payment = frappe._dict(
			salary_slip="SS-1",
			beneficiary_account_currency="TZS",
			transfer_amount=10,
			beneficiary_bank_sort_code="1",
			beneficiary_bank_name="Bank",
			beneficiary_bank_country_code=None,
			beneficiary_name="Someone",
			beneficiary_country="ke",
			beneficiary_account_number="123",
			beneficiary_account_type=0,
		)
		part = get_payment_part(payment)
		self.assertIn('<InstdAmt Ccy="TZS">10.00</InstdAmt>', part)
		self.assertIn("<Ctry>TZ</Ctry>", part)
		self.assertIn("<Ctry>KE</Ctry>", part)
		doc = self.make_initiation()
		self.assertEqual(strip_creation_time(get_xml(doc)), strip_creation_time(doc.xml))

	def test_submit_writes_outbox_file_and_processes_status_files(self):
		doc = self.make_initiation()
		doc.submit()
		outbox = os.path.join(self.temp_dir, "private/files/stanbic/outbox")
		files = os.listdir(outbox)
		self.assertEqual(len(files), 1)
		self.assertTrue(files[0].startswith("WASCO_H2H_Pain001v3_TZ_WASCO_"))
		with open(os.path.join(outbox, files[0])) as handle:
			self.assertEqual(handle.read(), doc.encrypted_xml)

		inbox = sftp.get_local_path(["private", "files", "stanbic", "inbox"])
		os.makedirs(inbox, exist_ok=True)
		with open(os.path.join(inbox, "ACK_1.xml"), "w") as handle:
			handle.write(ack_xml(doc.name))
		with open(os.path.join(inbox, "INTAUD_1.xml"), "w") as handle:
			handle.write(
				audit_xml(
					doc.name,
					[
						(self.slips[0].name, "<AddtlInf>Authorised</AddtlInf>"),
						(self.slips[1].name, "<Rsn>x</Rsn>"),
					],
				)
			)
		with open(os.path.join(inbox, "FINAUD_1.xml"), "w") as handle:
			handle.write(audit_xml(doc.name, [(self.slips[0].name, "<AddtlInf>Paid</AddtlInf>")]))
		with open(os.path.join(inbox, "ACK_missing.xml"), "w") as handle:
			handle.write(ack_xml("NO-SUCH-DOC"))
		with open(os.path.join(inbox, "notes.txt"), "w") as handle:
			handle.write("ignored")

		sftp.process_download_files()

		doc.reload()
		self.assertEqual(doc.stanbic_ack_status, "ACTC Accepted")
		self.assertEqual(doc.stanbic_ack_change, 0)
		self.assertEqual(doc.stanbic_intaud_change, 0)
		self.assertEqual(doc.stanbic_finaud_change, 0)
		self.assertEqual(
			json.loads(doc.stanbic_ack)["Document"]["CstmrPmtStsRpt"]["OrgnlGrpInfAndSts"]["GrpSts"], "ACTC"
		)
		statuses = {
			row.salary_slip: (row.stanbic_intaud_status, row.stanbic_finaud_status)
			for row in doc.stanbic_payments_info
		}
		self.assertEqual(statuses[self.slips[0].name], ('"Authorised"', '"Paid"'))
		self.assertEqual(statuses[self.slips[1].name], ('"STATUS NOT FOUND"', None))

		sftp.process_download_files()
		doc.reload()
		self.assertEqual(doc.stanbic_ack_status, "ACTC Accepted")

	def test_parse_xml(self):
		path = os.path.join(self.temp_dir, "sample.xml")
		with open(path, "w") as handle:
			handle.write(ack_xml("MSG-1", info="Fine"))
		parsed = parse_xml(path)
		self.assertEqual(parsed["Document"]["CstmrPmtStsRpt"]["OrgnlGrpInfAndSts"]["OrgnlMsgId"], "MSG-1")


class TestStanbicSftp(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.key = make_pgp_key()
		cls.setting = make_stanbic_setting(str(cls.key.pubkey))
		cls.temp_dir = tempfile.mkdtemp(prefix="csf_tz_sftp_")

	def make_client(self):
		with patch.object(sftp, "paramiko") as paramiko:
			client = sftp.Paramiko("host", "user", "/tmp/key.pem", 2222)
		ssh = paramiko.SSHClient.return_value
		ssh.connect.assert_called_once()
		self.assertEqual(ssh.connect.call_args.args, ("host",))
		self.assertEqual(ssh.connect.call_args.kwargs["port"], 2222)
		paramiko.RSAKey.from_private_key_file.assert_called_once_with("/tmp/key.pem")
		return client, ssh

	def test_download_and_cleanup(self):
		client, ssh = self.make_client()
		local = os.path.join(self.temp_dir, "inbox")
		remote_sftp = ssh.open_sftp.return_value
		remote_sftp.listdir.return_value = ["ACK_1.xml"]

		def fake_get(remote_path, local_path):
			with open(local_path, "w") as handle:
				handle.write(remote_path)

		remote_sftp.get.side_effect = fake_get
		self.assertEqual(client.download("/Inbox", local, cleanup=True), ["ACK_1.xml"])
		remote_sftp.remove.assert_called_once_with("/Inbox/ACK_1.xml")
		remote_sftp.close.assert_called_once()

		remote_sftp.listdir.side_effect = OSError("no connection")
		with self.assertRaisesRegex(frappe.ValidationError, "no connection"):
			client.download("/Inbox", local)

	def test_upload_execute_and_close(self):
		client, ssh = self.make_client()
		local = os.path.join(self.temp_dir, "outbox")
		os.makedirs(local, exist_ok=True)
		with open(os.path.join(local, "pain.xml"), "w") as handle:
			handle.write("<xml/>")
		remote_sftp = ssh.open_sftp.return_value
		self.assertEqual(client.upload(local, "/Outbox", cleanup=True), ["pain.xml"])
		remote_sftp.put.assert_called_once_with(os.path.join(local, "pain.xml"), "/Outbox/pain.xml")
		self.assertEqual(os.listdir(local), [])

		remote_sftp.put.side_effect = OSError("denied")
		with open(os.path.join(local, "pain.xml"), "w") as handle:
			handle.write("<xml/>")
		with self.assertRaisesRegex(frappe.ValidationError, "denied"):
			client.upload(local, "/Outbox")

		stdout, stderr = MagicMock(), MagicMock()
		stdout.read.return_value = b"ok"
		stderr.read.return_value = b""
		ssh.exec_command.return_value = (MagicMock(), stdout, stderr)
		self.assertEqual(client.execute("ls"), ("ok", ""))
		client.close()
		ssh.close.assert_called_once()

	def test_sync_uses_settings(self):
		with patch.object(sftp, "Paramiko") as paramiko_class:
			instance = paramiko_class.return_value
			instance.upload.return_value = ["up.xml"]
			instance.download.return_value = ["down.xml"]
			self.assertEqual(sftp.sync_stanbank_files(self.setting.name), (["up.xml"], ["down.xml"]))
		key_path = sftp.get_absolute_path("/private/files/stanbic_test_key.pem")
		paramiko_class.assert_called_with("sftp.example.com", "wasco", key_path, 2222)
		self.assertEqual(instance.upload.call_args.args[1], "/Outbox")
		self.assertEqual(instance.download.call_args.args[0], "/Inbox")
		self.assertEqual(instance.close.call_count, 2)

		with patch.object(sftp, "sync_stanbank_files") as sync:
			sftp.sync_all_stanbank_files()
		self.assertIn(self.setting.name, [call.args[0] for call in sync.call_args_list])

	def test_paths(self):
		site_root = os.path.join(frappe.utils.get_bench_path(), "sites", frappe.local.site)
		self.assertEqual(sftp.get_absolute_path("/files/a.txt"), f"{site_root}/public//files/a.txt")
		self.assertEqual(sftp.get_absolute_path("/private/files/a.txt"), f"{site_root}/private/files/a.txt")
		self.assertEqual(sftp.get_absolute_path("/tmp/a.txt"), "/tmp/a.txt")
		self.assertEqual(sftp.get_local_path(["private", "files", "x"]), f"{site_root}/private/files/x")
		self.assertEqual(sftp.get_local_path(), "/")
		self.assertEqual(sftp.get_site_path(), frappe.get_site_path("private", "files"))
		path = os.path.join(self.temp_dir, "new", "dir")
		sftp.create_dir_if_not_exists(path)
		self.assertTrue(os.path.isdir(path))
