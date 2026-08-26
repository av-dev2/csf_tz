import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from csf_tz.tests.import_fixtures import COMPANY, INR_BANK, INR_SUPPLIER

BANK = "_Test Import Bank"


class TestBankChargesDoctypes(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.get_doc({"doctype": "Bank", "bank_name": BANK}).insert(ignore_if_duplicate=True)
		cls.bank_account = frappe.get_doc(
			{
				"doctype": "Bank Account",
				"account_name": "_Test Charges Account",
				"bank": BANK,
				"is_company_account": 1,
				"company": COMPANY,
				"account": INR_BANK,
				"bank_supplier": INR_SUPPLIER,
			}
		).insert()
		if not frappe.db.exists("Item", "Bank Charges"):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": "Bank Charges",
					"item_name": "Bank Charges",
					"item_group": "_Test Item Group",
					"stock_uom": "_Test UOM",
					"is_stock_item": 0,
					"item_defaults": [
						{
							"company": COMPANY,
							"expense_account": "Bank Charges - _TC",
							"default_warehouse": "_Test Warehouse - _TC",
						}
					],
				}
			).insert()

	def make_bank_charges(self, *debits):
		return frappe.get_doc(
			{
				"doctype": "CSF TZ Bank Charges",
				"bank_account": self.bank_account.name,
				"currency": "INR",
				"bank_supplier": INR_SUPPLIER,
				"exchange_rate": 1,
				"account": INR_BANK,
				"posting_date": nowdate(),
				"company": COMPANY,
				"csf_tz_bank_charges_detail": [
					{"value_date": nowdate(), "debit_amount": amount, "reference_number": f"REF-{index}"}
					for index, amount in enumerate(debits)
				],
			}
		)

	def test_total_is_sum_of_positive_debits(self):
		charges = self.make_bank_charges(100, 50, -20)
		charges.insert()
		self.assertEqual(charges.total_bank_charges, 150)
		self.assertTrue(charges.name.startswith("CTBC-"))

	def test_submit_creates_payments_and_invoice(self):
		charges = self.make_bank_charges(100, 50)
		charges.insert()
		charges.submit()
		payments = frappe.get_all(
			"Payment Entry",
			filters={"party": INR_SUPPLIER, "reference_no": ["in", ["REF-0", "REF-1"]], "docstatus": 1},
			fields=["name", "paid_amount", "paid_from"],
		)
		self.assertEqual(sorted(row.paid_amount for row in payments), [50, 100])
		self.assertEqual({row.paid_from for row in payments}, {INR_BANK})
		self.assertEqual({row.ref_doctype for row in charges.csf_tz_bank_charges_detail}, {"Payment Entry"})
		self.assertEqual(
			{row.ref_docname for row in charges.csf_tz_bank_charges_detail}, {p.name for p in payments}
		)
		invoice = frappe.get_doc("Purchase Invoice", charges.ref_pi)
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(invoice.supplier, INR_SUPPLIER)
		self.assertEqual(invoice.items[0].item_code, "Bank Charges")
		self.assertEqual(invoice.grand_total, 150)

	def test_bank_charges_pattern(self):
		pattern = frappe.get_doc(
			{
				"doctype": "Bank Charges Pattern",
				"bank_account": self.bank_account.name,
				"bank_charges_pattern": "CHG",
			}
		).insert()
		self.assertTrue(pattern.name.startswith("CSFTZ-BCP-"))
		self.assertEqual(pattern.bank_charges_pattern, "CHG")
