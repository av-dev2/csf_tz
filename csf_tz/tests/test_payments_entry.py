import json

import frappe
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, getdate, nowdate

from csf_tz.csftz_hooks.customer import get_customer_total_unpaid_amount
from csf_tz.csftz_hooks.payment_entry import (
	get_outstanding_reference_documents,
	get_outstanding_sales_orders,
)
from csf_tz.tests.import_fixtures import (
	COMPANY,
	INR_BANK,
	INR_SUPPLIER,
	ORIGINAL_RATE,
	USD_SUPPLIER,
	make_foreign_purchase_invoice,
	make_plain_supplier_payment,
	make_purchase_invoice,
)

BANK_CHARGES_ACCOUNT = "Bank Charges - _TC"
CUSTOMER = "_Test Customer"


class TestPaymentEntryHooks(IntegrationTestCase):
	def set_company(self, **values):
		for field, value in values.items():
			original = frappe.db.get_value("Company", COMPANY, field)
			frappe.db.set_value("Company", COMPANY, field, value)
			self.addCleanup(frappe.db.set_value, "Company", COMPANY, field, original)
		frappe.clear_document_cache("Company", COMPANY)
		self.addCleanup(frappe.clear_document_cache, "Company", COMPANY)

	def test_unallocated_amount_is_restricted(self):
		self.set_company(restrict_unallocated_amount_for_supplier=1)
		payment = make_plain_supplier_payment(INR_SUPPLIER, 100, restrict_unallocated_amount_for_supplier=1)
		with self.assertRaisesRegex(frappe.ValidationError, "unallocated amount"):
			payment.insert()
		payment.restrict_unallocated_amount_for_supplier = 0
		payment.insert()
		self.assertEqual(payment.unallocated_amount, 100)

	def test_unallocated_amount_allowed_when_company_flag_is_off(self):
		self.set_company(restrict_unallocated_amount_for_supplier=0)
		payment = make_plain_supplier_payment(INR_SUPPLIER, 100, restrict_unallocated_amount_for_supplier=1)
		payment.insert()
		self.assertEqual(payment.unallocated_amount, 100)

	def test_bank_charges_require_company_account(self):
		self.set_company(default_bank_charges_account=None)
		payment = make_plain_supplier_payment(INR_SUPPLIER, 100, bank_charges=10)
		payment.insert()
		with self.assertRaisesRegex(frappe.ValidationError, "Default Bank Charges Account"):
			payment.submit()

	def test_bank_charges_journal_entry_on_submit(self):
		self.set_company(default_bank_charges_account=BANK_CHARGES_ACCOUNT)
		payment = make_plain_supplier_payment(INR_SUPPLIER, 100, bank_charges=15)
		payment.insert()
		payment.submit()
		self.assertTrue(payment.bank_charges_journal_entry)
		journal_entry = frappe.get_doc("Journal Entry", payment.bank_charges_journal_entry)
		self.assertEqual(journal_entry.docstatus, 1)
		self.assertEqual(journal_entry.voucher_type, "Bank Entry")
		self.assertEqual(journal_entry.cheque_no, payment.name)
		self.assertIn(payment.name, journal_entry.user_remark)
		amounts = {row.account: (row.debit, row.credit) for row in journal_entry.accounts}
		self.assertEqual(amounts[BANK_CHARGES_ACCOUNT], (15, 0))
		self.assertEqual(amounts[INR_BANK], (0, 15))

	def test_no_journal_entry_without_bank_charges(self):
		payment = make_plain_supplier_payment(INR_SUPPLIER, 100)
		payment.insert()
		payment.submit()
		self.assertFalse(payment.bank_charges_journal_entry)

	def supplier_args(self, supplier=INR_SUPPLIER, party_account="Creditors - _TC", **extra):
		args = {
			"party_type": "Supplier",
			"party": supplier,
			"party_account": party_account,
			"company": COMPANY,
		}
		args.update(extra)
		return args

	def test_get_outstanding_reference_documents(self):
		invoice = make_purchase_invoice(supplier=INR_SUPPLIER, rate=100, qty=2)
		rows = get_outstanding_reference_documents(json.dumps(self.supplier_args()))
		row = next(r for r in rows if r.get("voucher_no") == invoice.name)
		self.assertEqual(row["exchange_rate"], 1)
		self.assertEqual(getdate(row["posting_date"]), getdate(invoice.posting_date))
		self.assertIn("bill_no", row)
		self.assertEqual(row["outstanding_amount"], invoice.outstanding_amount)

	def test_get_outstanding_reference_documents_filters(self):
		invoice = make_purchase_invoice(supplier=INR_SUPPLIER, rate=100, qty=2)
		rows = get_outstanding_reference_documents(
			self.supplier_args(voucher_type="Purchase Invoice", voucher_no=invoice.name)
		)
		self.assertEqual([r.get("voucher_no") for r in rows], [invoice.name])
		later = self.supplier_args(
			from_posting_date=add_days(nowdate(), 1), to_posting_date=add_days(nowdate(), 2)
		)
		self.assertNotIn(
			invoice.name, [r.get("voucher_no") for r in get_outstanding_reference_documents(later)]
		)

	def test_get_outstanding_reference_documents_foreign_currency(self):
		invoice = make_foreign_purchase_invoice()
		rows = get_outstanding_reference_documents(
			self.supplier_args(USD_SUPPLIER, "_Test Payable USD - _TC")
		)
		row = next(r for r in rows if r.get("voucher_no") == invoice.name)
		self.assertEqual(row["exchange_rate"], ORIGINAL_RATE)

	def test_get_outstanding_reference_documents_disabled_or_member(self):
		make_purchase_invoice(supplier=INR_SUPPLIER, rate=100)
		frappe.db.set_single_value("CSF TZ Settings", "disable_get_outstanding_functionality", 1)
		self.addCleanup(
			frappe.db.set_single_value, "CSF TZ Settings", "disable_get_outstanding_functionality", 0
		)
		self.assertEqual(get_outstanding_reference_documents(self.supplier_args()), [])
		frappe.db.set_single_value("CSF TZ Settings", "disable_get_outstanding_functionality", 0)
		self.assertIsNone(get_outstanding_reference_documents({"party_type": "Member"}))

	def test_get_outstanding_reference_documents_blocked_supplier(self):
		make_purchase_invoice(supplier=INR_SUPPLIER, rate=100)
		self.addCleanup(frappe.db.set_value, "Supplier", INR_SUPPLIER, {"on_hold": 0, "hold_type": ""})
		frappe.db.set_value("Supplier", INR_SUPPLIER, {"on_hold": 1, "hold_type": "All"})
		self.assertEqual(get_outstanding_reference_documents(self.supplier_args()), [])
		frappe.db.set_value("Supplier", INR_SUPPLIER, {"hold_type": "Payments", "release_date": None})
		self.assertEqual(get_outstanding_reference_documents(self.supplier_args()), [])
		frappe.db.set_value("Supplier", INR_SUPPLIER, {"hold_type": "Invoices"})
		self.assertTrue(get_outstanding_reference_documents(self.supplier_args()))

	def test_get_outstanding_sales_orders(self):
		order = make_sales_order(customer=CUSTOMER, qty=2, rate=100)
		args = {
			"party_type": "Customer",
			"party": CUSTOMER,
			"party_account": "Debtors - _TC",
			"company": COMPANY,
			"posting_date": nowdate(),
		}
		rows = get_outstanding_sales_orders(json.dumps(args))
		row = next(r for r in rows if r.get("voucher_no") == order.name)
		self.assertEqual(row["voucher_type"], "Sales Order")
		self.assertEqual(getdate(row["posting_date"]), getdate(order.transaction_date))
		self.assertEqual(getdate(row["due_date"]), getdate(order.delivery_date))
		self.assertEqual(row["exchange_rate"], 1)
		with self.assertRaisesRegex(frappe.ValidationError, "only be fetched for Customer"):
			get_outstanding_sales_orders(self.supplier_args())
		self.assertIsNone(get_outstanding_sales_orders({"party_type": "Member"}))

	def make_customer_debit(self, amount):
		frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": COMPANY,
				"posting_date": nowdate(),
				"accounts": [
					{
						"account": "Debtors - _TC",
						"party_type": "Customer",
						"party": CUSTOMER,
						"debit_in_account_currency": amount,
					},
					{
						"account": "Sales - _TC",
						"cost_center": "_Test Cost Center - _TC",
						"credit_in_account_currency": amount,
					},
				],
			}
		).submit()

	def test_customer_total_unpaid_amount(self):
		def unpaid(company=None):
			return flt(str(get_customer_total_unpaid_amount(CUSTOMER, company)).replace(",", ""))

		before = unpaid(COMPANY)
		self.make_customer_debit(100)
		self.assertEqual(unpaid(COMPANY) - before, 100)
		self.assertGreaterEqual(unpaid(), unpaid(COMPANY))
		self.assertEqual(get_customer_total_unpaid_amount(None), 0)
