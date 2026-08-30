import base64
import hashlib
import json
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch

import frappe
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate
from pypdf import PdfWriter

from csf_tz.kcb import payments
from csf_tz.kcb.api import kcb_api
from csf_tz.kcb.doctype.kcb_payments_initiation.kcb_payments_initiation import _clean, _purpose
from csf_tz.kcb.payments import (
	make_kcb_payments_initiation_from_payment_entries,
	make_kcb_payments_initiation_from_payroll_entry,
)
from csf_tz.kcb.pgp import encrypt_pgp
from csf_tz.kcb.utils.crypto_utils import generate_checksum, sign_checksum_with_p12
from csf_tz.tests.integration_fixtures import (
	COMPANY,
	FakeResponse,
	insert_stub,
	make_p12,
	make_payroll_entry_stub,
	make_pgp_key,
	make_salary_slip_stub,
	pgp_decrypt,
)

BANK = "_Test KCB Bank"


def blank_pdf():
	writer = PdfWriter()
	writer.add_blank_page(width=72, height=72)
	buffer = BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


P12_PASSWORD = "secret"
EMPLOYEES = ["_T-Employee-00001", "_T-Employee-00002"]


def make_bank_account(account_name, **values):
	name = f"{account_name} - {BANK}"
	if frappe.db.exists("Bank Account", name):
		return frappe.get_doc("Bank Account", name)
	if not frappe.db.exists("Bank", BANK):
		frappe.get_doc({"doctype": "Bank", "bank_name": BANK}).insert()
	return frappe.get_doc(
		{"doctype": "Bank Account", "account_name": account_name, "bank": BANK, **values}
	).insert()


def make_p12_file():
	p12_bytes, private_key = make_p12(P12_PASSWORD)
	file_doc = frappe.get_doc(
		{"doctype": "File", "file_name": "csf_tz_kcb_test.p12", "is_private": 1, "content": p12_bytes}
	).insert()
	return file_doc.file_url, private_key


def configure_kcb_settings(public_key, p12_url, bank_account, **overrides):
	settings = frappe.get_single("KCB Settings")
	settings.update(
		{
			"enabled": 1,
			"token_url": "https://kcb.test/token",
			"file_details_submission_url": "https://kcb.test/details",
			"file_upload_url": "https://kcb.test/upload",
			"file_status_check_url": "https://kcb.test/status",
			"username": "kcbuser",
			"password": "kcbpass",
			"partner_code": "PARTNER",
			"processor_code": "PROC",
			"subsidiary_code": "SUB",
			"template_name": "TEMPLATE",
			"default_bank_account": bank_account,
			"pgp_public_key": public_key,
			"p12_file": p12_url,
			"p12_password": P12_PASSWORD,
			**overrides,
		}
	)
	settings.save(ignore_permissions=True)
	return settings


def make_payment_entry_stub(party_bank_account, amount=1000, docstatus=1, payment_type="Pay"):
	return insert_stub(
		{
			"doctype": "Payment Entry",
			"naming_series": "ACC-PAY-.YYYY.-",
			"payment_type": payment_type,
			"company": COMPANY,
			"posting_date": nowdate(),
			"party_type": "Supplier",
			"party": "_Test Supplier",
			"party_name": "_Test Supplier",
			"party_bank_account": party_bank_account,
			"paid_from_account_currency": "INR",
			"paid_to_account_currency": "INR",
			"paid_amount": amount,
			"received_amount": amount,
		},
		docstatus=docstatus,
	)


class TestKCBPayments(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.key = make_pgp_key()
		cls.p12_url, cls.private_key = make_p12_file()
		cls.company_account = make_bank_account(
			"_Test KCB Company",
			is_company_account=1,
			company=COMPANY,
			account="_Test Bank - _TC",
			bank_account_no="0011223344",
		)
		cls.supplier_account = make_bank_account(
			"_Test KCB Supplier",
			party_type="Supplier",
			party="_Test Supplier",
			bank_account_no="5566778899",
			kcb_beneficiary_clearing_code="010101",
		)
		cls.bad_supplier_account = make_bank_account(
			"_Test KCB Supplier Bad", party_type="Supplier", party="_Test Supplier", bank_account_no="1"
		)
		cls.settings = configure_kcb_settings(str(cls.key.pubkey), cls.p12_url, cls.company_account.name)
		for employee in EMPLOYEES:
			frappe.db.set_value(
				"Employee", employee, {"bank_ac_no": "9988776655", "kcb_beneficiary_clearing_code": "020202"}
			)
		cls.pdf_patch = patch.object(payments, "get_pdf", return_value=blank_pdf())
		cls.pdf_patch.start()
		cls.addClassCleanup(cls.pdf_patch.stop)

	def setUp(self):
		frappe.cache().delete_value("kcb_token")
		frappe.cache().delete_value("kcb_token_expiry")

	def attachments(self, name):
		return frappe.get_all(
			"File",
			filters={"attached_to_doctype": "KCB Payments Initiation", "attached_to_name": name},
			pluck="file_name",
		)

	def make_supplier_batch(self):
		entries = [
			make_payment_entry_stub(self.supplier_account.name),
			make_payment_entry_stub(self.supplier_account.name, 500),
		]
		name = make_kcb_payments_initiation_from_payment_entries([pe.name for pe in entries])
		return frappe.get_doc("KCB Payments Initiation", name), entries

	def test_helpers(self):
		self.assertEqual(_clean(None), "")
		self.assertEqual(_clean(" a|b\r\nc  d "), "a b c d")
		self.assertEqual(_purpose("x" * 40), "x" * 25)
		self.assertEqual(generate_checksum("abc"), generate_checksum(b"abc"))
		self.assertEqual(generate_checksum("abc"), hashlib.sha256(b"abc").hexdigest())
		self.assertEqual(payments._validate_single_value(["INR", "INR", None], "Currency"), "INR")
		self.assertEqual(payments._validate_single_value([], "Currency"), "")
		self.assertRaisesRegex(
			frappe.ValidationError,
			"must be the same",
			payments._validate_single_value,
			["INR", "USD"],
			"Currency",
		)
		self.assertEqual(payments._get_bank_account_details(None), {})
		self.assertEqual(
			payments._get_bank_account_details(self.company_account.name)["bank_account_no"], "0011223344"
		)
		self.assertEqual(kcb_api.is_kcb_enabled(), 1)

	def test_pgp_and_p12_signature(self):
		encrypted = encrypt_pgp(b"file bytes", str(self.key.pubkey))
		self.assertEqual(pgp_decrypt(self.key, encrypted), "file bytes")
		self.assertEqual(pgp_decrypt(self.key, encrypt_pgp("text", str(self.key.pubkey))), "text")

		checksum = generate_checksum("payload")
		signature = sign_checksum_with_p12(checksum)
		self.private_key.public_key().verify(
			base64.b64decode(signature), checksum.encode(), padding.PKCS1v15(), hashes.SHA256()
		)

	def test_sign_checksum_requires_p12_configuration(self):
		frappe.db.set_single_value("KCB Settings", "p12_file", "")
		try:
			self.assertRaisesRegex(
				frappe.ValidationError, "P12 file is missing", sign_checksum_with_p12, "abc"
			)
		finally:
			frappe.db.set_single_value("KCB Settings", "p12_file", self.p12_url)

	def test_supplier_batch_creates_files_and_signature(self):
		doc, entries = self.make_supplier_batch()
		self.assertEqual(doc.payment_type, "Supplier")
		self.assertEqual(doc.debit_account, "0011223344")
		self.assertEqual(doc.currency, "INR")
		self.assertEqual(doc.total_amount, 1500)
		self.assertEqual(len(doc.kcb_payments_initiation_info), 2)
		row = doc.kcb_payments_initiation_info[0]
		self.assertEqual((row.source_doctype, row.source_name), ("Payment Entry", entries[0].name))
		self.assertEqual(
			(row.transaction_code, row.beneficiary_account, row.beneficiary_clearing_code),
			("59", "5566778899", "010101"),
		)

		self.assertEqual(
			sorted(self.attachments(doc.name)),
			sorted([f"SUP-{doc.name}-Supplier-Summary.pdf", f"{doc.name}.txt", f"{doc.name}.txt.gpg"]),
		)
		self.assertTrue(doc.payment_file.endswith(f"{doc.name}.txt"))
		self.assertTrue(doc.encrypted_file.endswith(f"{doc.name}.txt.gpg"))
		text_content = frappe.get_doc("File", {"file_url": doc.payment_file}).get_content()
		self.assertTrue(text_content.startswith("Debit Account|Beneficiary Name|"))
		self.assertTrue(text_content.endswith("\n1500.0"))
		self.assertIn(
			f"0011223344|_Test Supplier|59|1000.0|INR|5566778899|010101|{entries[0].name}", text_content
		)
		self.assertEqual(doc.file_checksum, generate_checksum(text_content))
		self.private_key.public_key().verify(
			base64.b64decode(doc.checksum_signature),
			doc.file_checksum.encode(),
			padding.PKCS1v15(),
			hashes.SHA256(),
		)
		gpg_content = frappe.get_doc("File", {"file_url": doc.encrypted_file}).get_content()
		self.assertEqual(pgp_decrypt(self.key, gpg_content), text_content)

		self.assertRaisesRegex(
			frappe.ValidationError,
			"already exists",
			make_kcb_payments_initiation_from_payment_entries,
			json.dumps([entries[0].name]),
		)

	def test_supplier_batch_validations(self):
		self.assertRaisesRegex(
			frappe.ValidationError, "at least one", make_kcb_payments_initiation_from_payment_entries, []
		)
		draft = make_payment_entry_stub(self.supplier_account.name, docstatus=0)
		self.assertRaisesRegex(
			frappe.ValidationError,
			"Only submitted Pay",
			make_kcb_payments_initiation_from_payment_entries,
			[draft.name],
		)
		receive = make_payment_entry_stub(self.supplier_account.name, payment_type="Receive")
		self.assertRaisesRegex(
			frappe.ValidationError,
			"Only submitted Pay",
			make_kcb_payments_initiation_from_payment_entries,
			[receive.name],
		)
		no_account = make_payment_entry_stub(None)
		self.assertRaisesRegex(
			frappe.ValidationError,
			"Missing supplier bank account",
			make_kcb_payments_initiation_from_payment_entries,
			[no_account.name],
		)
		bad_code = make_payment_entry_stub(self.bad_supplier_account.name)
		self.assertRaisesRegex(
			frappe.ValidationError,
			"Missing beneficiary clearing code",
			make_kcb_payments_initiation_from_payment_entries,
			[bad_code.name],
		)
		frappe.db.set_value(
			"Bank Account", self.bad_supplier_account.name, "kcb_beneficiary_clearing_code", "12"
		)
		self.assertRaisesRegex(
			frappe.ValidationError,
			"must be 6 characters",
			make_kcb_payments_initiation_from_payment_entries,
			[bad_code.name],
		)
		frappe.db.set_value(
			"Bank Account", self.bad_supplier_account.name, "kcb_beneficiary_clearing_code", ""
		)

		good = make_payment_entry_stub(self.supplier_account.name)
		frappe.db.set_single_value("KCB Settings", "default_bank_account", None)
		try:
			self.assertRaisesRegex(
				frappe.ValidationError,
				"Company bank account is missing",
				make_kcb_payments_initiation_from_payment_entries,
				[good.name],
			)
		finally:
			frappe.db.set_single_value("KCB Settings", "default_bank_account", self.company_account.name)

		frappe.db.set_single_value("KCB Settings", "enabled", 0)
		try:
			self.assertRaisesRegex(
				frappe.ValidationError,
				"disabled",
				make_kcb_payments_initiation_from_payment_entries,
				[good.name],
			)
			self.assertEqual(kcb_api.is_kcb_enabled(), 0)
		finally:
			frappe.db.set_single_value("KCB Settings", "enabled", 1)

	def test_payroll_batch(self):
		payroll_entry = make_payroll_entry_stub()
		self.assertRaisesRegex(
			frappe.ValidationError,
			"No Salary Slips found",
			make_kcb_payments_initiation_from_payroll_entry,
			payroll_entry.name,
		)
		slips = [
			make_salary_slip_stub(payroll_entry, EMPLOYEES[0], 700),
			make_salary_slip_stub(payroll_entry, EMPLOYEES[1], 300, docstatus=0),
		]
		self.assertRaisesRegex(
			frappe.ValidationError,
			"Submit all Salary Slips first",
			make_kcb_payments_initiation_from_payroll_entry,
			payroll_entry.name,
		)
		frappe.db.set_value("Salary Slip", slips[1].name, "docstatus", 1)

		name = make_kcb_payments_initiation_from_payroll_entry(payroll_entry.name)
		doc = frappe.get_doc("KCB Payments Initiation", name)
		self.assertEqual(doc.payment_type, "Salary")
		self.assertEqual(doc.total_amount, 1000)
		self.assertEqual(str(doc.posting_date), str(payroll_entry.posting_date))
		self.assertEqual([row.transaction_code for row in doc.kcb_payments_initiation_info], ["58", "58"])
		self.assertEqual(
			{row.my_ref for row in doc.kcb_payments_initiation_info}, {slips[0].name, slips[1].name}
		)
		self.assertEqual(doc.kcb_payments_initiation_info[0].beneficiary_clearing_code, "020202")
		self.assertIn(f"SUP-{name}-Payroll-Summary.pdf", self.attachments(name))

		self.assertRaisesRegex(
			frappe.ValidationError,
			"already exists",
			make_kcb_payments_initiation_from_payroll_entry,
			payroll_entry.name,
		)

	def test_payroll_batch_employee_validations(self):
		payroll_entry = make_payroll_entry_stub()
		make_salary_slip_stub(payroll_entry, EMPLOYEES[0], 700)
		frappe.db.set_value("Employee", EMPLOYEES[0], "bank_ac_no", "")
		try:
			self.assertRaisesRegex(
				frappe.ValidationError,
				"Missing employee bank account",
				make_kcb_payments_initiation_from_payroll_entry,
				payroll_entry.name,
			)
			frappe.db.set_value(
				"Employee", EMPLOYEES[0], {"bank_ac_no": "1", "kcb_beneficiary_clearing_code": "12"}
			)
			self.assertRaisesRegex(
				frappe.ValidationError,
				"must be 6 characters",
				make_kcb_payments_initiation_from_payroll_entry,
				payroll_entry.name,
			)
		finally:
			frappe.db.set_value(
				"Employee",
				EMPLOYEES[0],
				{"bank_ac_no": "9988776655", "kcb_beneficiary_clearing_code": "020202"},
			)

	def test_before_save_requires_pgp_key(self):
		frappe.db.set_single_value("KCB Settings", "pgp_public_key", "")
		try:
			entry = make_payment_entry_stub(self.supplier_account.name)
			self.assertRaisesRegex(
				frappe.ValidationError,
				"PGP public key is missing",
				make_kcb_payments_initiation_from_payment_entries,
				[entry.name],
			)
		finally:
			frappe.db.set_single_value("KCB Settings", "pgp_public_key", str(self.key.pubkey))

	def test_get_kcb_token_caches_and_refreshes(self):
		with patch(
			"requests.post", return_value=FakeResponse(200, {"access_token": "tok1", "expires_in": 3600})
		) as post:
			self.assertEqual(kcb_api.get_kcb_token(), "tok1")
			self.assertEqual(kcb_api.get_kcb_token(), "tok1")
		self.assertEqual(post.call_count, 1)
		self.assertEqual(post.call_args.kwargs["auth"], ("kcbuser", "kcbpass"))

		frappe.cache().set_value(
			"kcb_token_expiry", (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		)
		with patch(
			"requests.post",
			return_value=FakeResponse(200, {"bearer_token": "tok2", "expires_in_seconds": 120}),
		):
			self.assertEqual(kcb_api.get_kcb_token(), "tok2")

		frappe.cache().delete_value("kcb_token")
		with patch("requests.post", return_value=FakeResponse(200, {})):
			self.assertRaisesRegex(frappe.ValidationError, "Token generation failed", kcb_api.get_kcb_token)
		with patch("requests.post", return_value=FakeResponse(500, {}, text="boom")):
			self.assertRaisesRegex(frappe.ValidationError, "boom", kcb_api.get_kcb_token)

		frappe.db.set_single_value("KCB Settings", "username", "")
		try:
			self.assertRaisesRegex(frappe.ValidationError, "username/password", kcb_api.get_kcb_token)
		finally:
			frappe.db.set_single_value("KCB Settings", "username", "kcbuser")

	def test_submit_uploads_file_details_and_content(self):
		doc, _entries = self.make_supplier_batch()
		frappe.cache().set_value("kcb_token", "tok")
		frappe.cache().set_value(
			"kcb_token_expiry", (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		)

		with patch("requests.post", return_value=FakeResponse(200, {"status": "ok"})) as post:
			doc.submit()
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(post.call_count, 2)
		details_call, upload_call = post.call_args_list
		payload = details_call.kwargs["json"]
		originator = frappe.db.get_value("KCB Payments Initiation", doc.name, "originator_conversation_id")
		self.assertTrue(originator)
		self.assertEqual(payload["originatorConversationID"], originator)
		self.assertEqual(payload["fileName"], f"{doc.name}.txt.gpg")
		self.assertEqual(payload["supportingFilesNames"], f"SUP-{doc.name}-Supplier-Summary.pdf")
		self.assertEqual(payload["checkSum"], doc.file_checksum)
		self.assertEqual(payload["partnerCode"], "PARTNER")
		self.assertEqual(details_call.kwargs["headers"]["Authorization"], "Bearer tok")

		files = upload_call.kwargs["files"]
		self.assertEqual([entry[0] for entry in files], ["files", "files", "originatorConversationID"])
		self.assertEqual(files[0][1][0], f"{doc.name}.txt.gpg")
		self.assertEqual(files[-1][1], (None, originator))

		with patch("requests.post", return_value=FakeResponse(200, {"fileStatus": "PROCESSED"})) as post:
			self.assertEqual(kcb_api.check_file_status(doc.name), {"fileStatus": "PROCESSED"})
		self.assertEqual(post.call_args.kwargs["json"]["fileName"], f"{doc.name}.txt.gpg")
		with patch("requests.post", return_value=FakeResponse(400, {}, text="bad")):
			self.assertRaisesRegex(
				frappe.ValidationError, "status check failed", kcb_api.check_file_status, doc.name
			)

	def test_submission_errors(self):
		doc, _entries = self.make_supplier_batch()
		frappe.cache().set_value("kcb_token", "tok")
		frappe.cache().set_value(
			"kcb_token_expiry", (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		)
		self.assertRaisesRegex(
			frappe.ValidationError, "Originator Conversation ID", kcb_api.check_file_status, doc.name
		)
		with patch("requests.post", return_value=FakeResponse(500, {}, text="details down")):
			self.assertRaisesRegex(frappe.ValidationError, "details down", kcb_api.submit_file_details, doc)
		self.assertTrue(
			frappe.db.get_value("KCB Payments Initiation", doc.name, "originator_conversation_id")
		)
		with patch("requests.post", return_value=FakeResponse(500, {}, text="upload down")):
			self.assertRaisesRegex(frappe.ValidationError, "upload down", kcb_api.upload_encrypted_file, doc)

		pdf = frappe.get_doc("File", {"file_name": f"SUP-{doc.name}-Supplier-Summary.pdf"})
		pdf.delete()
		doc.reload()
		self.assertRaisesRegex(
			frappe.ValidationError, "supporting document", kcb_api._get_supporting_file_docs, doc
		)

		doc.db_set("encrypted_file", "")
		self.assertRaisesRegex(
			frappe.ValidationError, "Encrypted file is missing", kcb_api.check_file_status, doc.name
		)
