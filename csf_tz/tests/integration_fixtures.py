"""Shared fakes and record stubs for the csf_tz integration tests."""

import datetime
import json

import frappe
import pgpy
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from frappe.utils import nowdate
from pgpy.constants import (
	CompressionAlgorithm,
	HashAlgorithm,
	KeyFlags,
	PubKeyAlgorithm,
	SymmetricKeyAlgorithm,
)

COMPANY = "_Test Company"


class FakeResponse:
	"""Minimal stand-in for requests.Response."""

	def __init__(self, status_code=200, body=None, text=None):
		self.status_code = status_code
		self.body = body
		self.text = text if text is not None else json.dumps(body or {})

	def json(self):
		if self.body is None:
			raise ValueError("no json body")
		return self.body

	def raise_for_status(self):
		if self.status_code >= 400:
			raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


def make_pgp_key():
	"""Return a fresh private PGP key; str(key.pubkey) is the armored public key."""
	key = pgpy.PGPKey.new(PubKeyAlgorithm.RSAEncryptOrSign, 2048)
	key.add_uid(
		pgpy.PGPUID.new("csf_tz test"),
		usage={KeyFlags.Sign, KeyFlags.EncryptCommunications, KeyFlags.EncryptStorage},
		hashes=[HashAlgorithm.SHA256],
		ciphers=[SymmetricKeyAlgorithm.AES256],
		compression=[CompressionAlgorithm.ZLIB],
	)
	return key


def pgp_decrypt(key, armored_message):
	return key.decrypt(pgpy.PGPMessage.from_blob(armored_message)).message


def make_p12(password):
	"""Return (p12_bytes, private_key) for a self-signed certificate."""
	private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
	subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "csf_tz test")])
	now = datetime.datetime.now(datetime.timezone.utc)
	certificate = (
		x509.CertificateBuilder()
		.subject_name(subject)
		.issuer_name(subject)
		.public_key(private_key.public_key())
		.serial_number(x509.random_serial_number())
		.not_valid_before(now)
		.not_valid_after(now + datetime.timedelta(days=1))
		.sign(private_key, hashes.SHA256())
	)
	p12_bytes = pkcs12.serialize_key_and_certificates(
		b"csf_tz",
		private_key,
		certificate,
		None,
		serialization.BestAvailableEncryption(password.encode()),
	)
	return p12_bytes, private_key


def insert_stub(values, docstatus=0):
	"""Insert a record without running its controller validations."""
	doc = frappe.get_doc(values)
	doc.flags.ignore_validate = True
	doc.flags.ignore_permissions = True
	doc.insert(ignore_mandatory=True, ignore_links=True)
	if docstatus:
		frappe.db.set_value(doc.doctype, doc.name, "docstatus", docstatus, update_modified=False)
		doc.reload()
	return doc


def make_payroll_entry_stub(company=COMPANY, currency="INR"):
	return insert_stub(
		{
			"doctype": "Payroll Entry",
			"company": company,
			"posting_date": nowdate(),
			"start_date": nowdate(),
			"end_date": nowdate(),
			"currency": currency,
			"exchange_rate": 1,
			"payroll_frequency": "Monthly",
		}
	)


def make_salary_slip_stub(payroll_entry, employee, net_pay, currency="INR", docstatus=1):
	return insert_stub(
		{
			"doctype": "Salary Slip",
			"employee": employee,
			"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
			"company": payroll_entry.company,
			"payroll_entry": payroll_entry.name,
			"posting_date": nowdate(),
			"start_date": nowdate(),
			"end_date": nowdate(),
			"currency": currency,
			"exchange_rate": 1,
			"net_pay": net_pay,
			"gross_pay": net_pay,
		},
		docstatus=docstatus,
	)
