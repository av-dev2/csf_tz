import frappe
from frappe.tests import IntegrationTestCase

from csf_tz.custom_api import (
	make_withholding_tax_gl_entries_for_purchase,
	make_withholding_tax_gl_entries_for_sales,
)
from csf_tz.tests.custom_api_helpers import (
	COMPANY,
	CUSTOMER,
	SUPPLIER,
	disable_db_commit,
	make_purchase_invoice,
	make_sales_invoice,
	make_test_item,
)


def journal_rows(reference_type, reference_name):
	return frappe.get_all(
		"Journal Entry Account",
		filters={"reference_type": reference_type, "reference_name": reference_name},
		fields=["parent", "account", "party", "debit_in_account_currency", "credit_in_account_currency"],
	)


class TestSalesWithholdingTax(IntegrationTestCase):
	"""on_submit hook: withholding tax receivable Journal Entry per Sales Invoice item."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_test_item("_CSF WTax Sales Item", withholding_tax_rate_on_sales=5)

	def setUp(self):
		disable_db_commit(self)
		frappe.db.set_value(
			"Company",
			COMPANY,
			{
				"default_withholding_receivable_account": "_Test Receivable - _TC",
				"auto_create_for_sales_withholding": 1,
				"auto_submit_for_sales_withholding": 0,
			},
		)

	def test_journal_entry_is_created_on_submit(self):
		invoice = make_sales_invoice(item_code=self.item.name, qty=2, rate=100)
		rows = journal_rows("Sales Invoice", invoice.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].party, rows[0].credit_in_account_currency), (CUSTOMER, 10))
		journal = frappe.get_doc("Journal Entry", rows[0].parent)
		self.assertEqual((journal.docstatus, journal.voucher_type), (0, "Contra Entry"))
		self.assertEqual(journal.accounts[1].account, "_Test Receivable - _TC")
		self.assertEqual(journal.accounts[1].debit_in_account_currency, 10)
		invoice.reload()
		self.assertEqual(invoice.items[0].withholding_tax_entry, journal.name)
		self.assertEqual(invoice.items[0].csf_tz_wtax_jv_created, 1)

	def test_journal_entry_is_submitted_when_configured(self):
		frappe.db.set_value("Company", COMPANY, "auto_submit_for_sales_withholding", 1)
		invoice = make_sales_invoice(item_code=self.item.name, qty=2, rate=100)
		journal_name = journal_rows("Sales Invoice", invoice.name)[0].parent
		self.assertEqual(frappe.db.get_value("Journal Entry", journal_name, "docstatus"), 1)

	def test_skipped_when_company_flag_is_off(self):
		frappe.db.set_value("Company", COMPANY, "auto_create_for_sales_withholding", 0)
		invoice = make_sales_invoice(item_code=self.item.name, qty=2, rate=100)
		self.assertEqual(journal_rows("Sales Invoice", invoice.name), [])

	def test_throws_without_receivable_account(self):
		frappe.db.set_value("Company", COMPANY, "default_withholding_receivable_account", None)
		invoice = make_sales_invoice(item_code=self.item.name, do_not_submit=True)
		self.assertRaisesRegex(frappe.ValidationError, "Withholding Receivable Account", invoice.submit)

	def test_front_end_call_creates_once(self):
		frappe.db.set_value("Company", COMPANY, "auto_create_for_sales_withholding", 0)
		invoice = make_sales_invoice(item_code=self.item.name, qty=2, rate=100)
		frappe.db.set_value("Company", COMPANY, "auto_create_for_sales_withholding", 1)
		make_withholding_tax_gl_entries_for_sales(invoice.as_json(), "From Front End")
		self.assertEqual(len(journal_rows("Sales Invoice", invoice.name)), 1)
		invoice.reload()
		make_withholding_tax_gl_entries_for_sales(invoice.as_json(), "From Front End")
		self.assertEqual(len(journal_rows("Sales Invoice", invoice.name)), 1)


class TestPurchaseWithholdingTax(IntegrationTestCase):
	"""on_submit hook: withholding tax payable Journal Entry per Purchase Invoice item."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_test_item("_CSF WTax Purchase Item", witholding_tax_rate_on_purchase=2)

	def setUp(self):
		disable_db_commit(self)
		frappe.db.set_value(
			"Company",
			COMPANY,
			{
				"default_withholding_payable_account": "_Test Payable - _TC",
				"auto_create_for_purchase_withholding": 1,
				"auto_submit_for_purchase_withholding": 0,
			},
		)

	def test_journal_entry_is_created_on_submit(self):
		invoice = make_purchase_invoice(item_code=self.item.name, qty=2, rate=50)
		rows = journal_rows("Purchase Invoice", invoice.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].party, rows[0].debit_in_account_currency), (SUPPLIER, 2))
		journal = frappe.get_doc("Journal Entry", rows[0].parent)
		self.assertEqual(journal.accounts[1].account, "_Test Payable - _TC")
		self.assertEqual(journal.accounts[1].credit_in_account_currency, 2)
		invoice.reload()
		self.assertEqual(invoice.items[0].withholding_tax_entry, journal.name)

	def test_journal_entry_is_submitted_when_configured(self):
		frappe.db.set_value("Company", COMPANY, "auto_submit_for_purchase_withholding", 1)
		invoice = make_purchase_invoice(item_code=self.item.name, qty=2, rate=50)
		journal_name = journal_rows("Purchase Invoice", invoice.name)[0].parent
		self.assertEqual(frappe.db.get_value("Journal Entry", journal_name, "docstatus"), 1)

	def test_skipped_when_company_flag_is_off(self):
		frappe.db.set_value("Company", COMPANY, "auto_create_for_purchase_withholding", 0)
		invoice = make_purchase_invoice(item_code=self.item.name)
		self.assertEqual(journal_rows("Purchase Invoice", invoice.name), [])

	def test_throws_without_payable_account(self):
		frappe.db.set_value("Company", COMPANY, "default_withholding_payable_account", None)
		invoice = make_purchase_invoice(item_code=self.item.name, do_not_submit=True)
		self.assertRaisesRegex(frappe.ValidationError, "Withholding Payable Account", invoice.submit)

	def test_front_end_call(self):
		frappe.db.set_value("Company", COMPANY, "auto_create_for_purchase_withholding", 0)
		invoice = make_purchase_invoice(item_code=self.item.name, qty=2, rate=50)
		frappe.db.set_value("Company", COMPANY, "auto_create_for_purchase_withholding", 1)
		make_withholding_tax_gl_entries_for_purchase(invoice.as_json(), "From Front End")
		self.assertEqual(len(journal_rows("Purchase Invoice", invoice.name)), 1)
