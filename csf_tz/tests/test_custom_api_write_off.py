import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from csf_tz.custom_api import create_write_off_jv_pe, create_write_off_jv_pi, create_write_off_jv_si
from csf_tz.tests.custom_api_helpers import (
	COMPANY,
	CUSTOMER,
	SUPPLIER,
	disable_db_commit,
	make_purchase_invoice,
	make_sales_invoice,
	set_csf_settings,
)

WRITE_OFF_ACCOUNT = "Write Off - _TC"


def make_payment_entry(payment_type):
	receive = payment_type == "Receive"
	entry = frappe.get_doc(
		doctype="Payment Entry",
		payment_type=payment_type,
		party_type="Customer" if receive else "Supplier",
		party=CUSTOMER if receive else SUPPLIER,
		company=COMPANY,
		posting_date=nowdate(),
		mode_of_payment="Cash",
		paid_from="Debtors - _TC" if receive else "Cash - _TC",
		paid_to="Cash - _TC" if receive else "Creditors - _TC",
		paid_amount=100,
		received_amount=100,
		reference_no="CSF-WO",
		reference_date=nowdate(),
	)
	entry.insert()
	entry.submit()
	return entry


class TestWriteOffJournalEntries(IntegrationTestCase):
	"""Write-off Journal Entries clear invoice outstanding and payment unallocated amounts."""

	def setUp(self):
		disable_db_commit(self)
		set_csf_settings(enable_write_off_jv_si=1, enable_write_off_jv_pi=1, enable_write_off_jv_pe=1)

	def test_sales_invoice_write_off(self):
		invoice = make_sales_invoice(rate=100)
		journal_name = create_write_off_jv_si(invoice.name, WRITE_OFF_ACCOUNT)
		journal = frappe.get_doc("Journal Entry", journal_name)
		self.assertEqual((journal.docstatus, journal.voucher_type), (1, "Write Off Entry"))
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount"), 0)
		self.assertRaisesRegex(
			frappe.ValidationError, "No outstanding", create_write_off_jv_si, invoice.name, WRITE_OFF_ACCOUNT
		)

	def test_sales_invoice_write_off_disabled(self):
		set_csf_settings(enable_write_off_jv_si=0)
		invoice = make_sales_invoice(rate=100)
		self.assertIsNone(create_write_off_jv_si(invoice.name, WRITE_OFF_ACCOUNT))
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount"), 100)

	def test_purchase_invoice_write_off(self):
		invoice = make_purchase_invoice(rate=50)
		journal_name = create_write_off_jv_pi(invoice.name, WRITE_OFF_ACCOUNT)
		self.assertEqual(frappe.db.get_value("Journal Entry", journal_name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Purchase Invoice", invoice.name, "outstanding_amount"), 0)
		set_csf_settings(enable_write_off_jv_pi=0)
		self.assertIsNone(create_write_off_jv_pi(invoice.name, WRITE_OFF_ACCOUNT))

	def test_payment_entry_write_off_receive_and_pay(self):
		for payment_type in ("Receive", "Pay"):
			entry = make_payment_entry(payment_type)
			self.assertEqual(entry.unallocated_amount, 100)
			journal_name = create_write_off_jv_pe(entry.name, WRITE_OFF_ACCOUNT)
			self.assertEqual(frappe.db.get_value("Journal Entry", journal_name, "docstatus"), 1)
			entry.reload()
			self.assertEqual(entry.unallocated_amount, 0)
			self.assertEqual(entry.references[0].reference_name, journal_name)
			self.assertRaisesRegex(
				frappe.ValidationError,
				"No unallocated",
				create_write_off_jv_pe,
				entry.name,
				WRITE_OFF_ACCOUNT,
			)

	def test_payment_entry_write_off_disabled(self):
		set_csf_settings(enable_write_off_jv_pe=0)
		entry = make_payment_entry("Receive")
		self.assertIsNone(create_write_off_jv_pe(entry.name, WRITE_OFF_ACCOUNT))
