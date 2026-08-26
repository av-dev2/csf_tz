import frappe
from erpnext.selling.doctype.quotation.test_quotation import make_quotation
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from csf_tz.tests.report_fixtures import (
	COMPANY,
	ITEM,
	as_dicts,
	date_range,
	receive_stock,
	run_report,
	sell_stock,
)


def make_lead_quotation():
	lead = frappe.get_all("Lead", limit=1, pluck="name")[0]
	quotation = frappe.get_doc(
		{
			"doctype": "Quotation",
			"quotation_to": "Lead",
			"party_name": lead,
			"company": COMPANY,
			"transaction_date": today(),
			"items": [{"item_code": ITEM, "qty": 3, "rate": 100, "warehouse": "_Test Warehouse - _TC"}],
		}
	).insert()
	quotation.submit()
	return quotation


class TestSalesReports(IntegrationTestCase):
	"""Runs the sales, marketing and system listing reports of csf_tz."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		receive_stock(qty=20, rate=100)
		cls.sales_invoice = sell_stock(qty=2, rate=500)
		cls.customer_quotation = make_quotation(qty=5, rate=100)
		cls.lead_quotation = make_lead_quotation()

	def test_av_sales_invoice_trend(self):
		columns, rows = run_report(
			"AV Sales Invoice Trend",
			{
				"period": "Monthly",
				"based_on": "Item",
				"fiscal_year": "_Test Fiscal Year 2026",
				"company": COMPANY,
			},
		)
		self.assertEqual(columns[-1]["fieldname"], "warehouse")
		rows = as_dicts(columns, rows)
		item_rows = [r for r in rows if r.get("item") == ITEM]
		self.assertEqual(len(item_rows), 1, rows)
		self.assertGreater(item_rows[0]["total_available_qty"], 0)

	def test_brand_sales_report(self):
		columns, rows = run_report("Brand Sales Report", date_range())
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_customer_loan_assistance_report_needs_doctype(self):
		if frappe.db.exists("DocType", "Customer Loan Assistance"):
			self.skipTest("Customer Loan Assistance is installed")
		with self.assertRaises(frappe.ValidationError):
			run_report("Customer Loan Assistance report", date_range())

	def test_items_marked_for_delivery_needs_custom_field(self):
		if frappe.get_meta("Sales Invoice Item").has_field("is_marked"):
			self.skipTest("is_marked field is installed")
		with self.assertRaises(frappe.ValidationError):
			run_report("Items Marked For Delivery", {})

	def test_item_wise_leads_report(self):
		columns, rows = run_report("Item Wise Leads Report", date_range())
		rows = as_dicts(columns, rows)
		item_rows = [r for r in rows if r["item_code"] == ITEM]
		self.assertEqual(len(item_rows), 1)
		self.assertEqual(item_rows[0]["total_qty"], 8)
		self.assertEqual(item_rows[0]["quotations"], 2)
		self.assertEqual(item_rows[0]["customers"], 1)
		self.assertEqual(item_rows[0]["leads"], 1)

	def test_previous_ams_customer_report(self):
		columns, rows = run_report("Previous Ams Customer Report", date_range())
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_sales_details_report(self):
		columns, rows = run_report("Sales Details Report", date_range())
		self.assertTrue(any(self.sales_invoice.name in str(row) for row in rows))

	def test_spare_sales_report(self):
		columns, rows = run_report("Spare Sales Report", date_range())
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_parent_child_relationship(self):
		columns, rows = run_report(
			"Parent Child Relationship", {"is_submittable": 1, "is_table": 0, "module": "Accounts"}
		)
		self.assertTrue(any("Accounts-Sales Invoice" in str(row) for row in rows))

	def test_role_and_user_listing(self):
		columns, rows = run_report("Role Permission Listing", {})
		self.assertTrue(rows)
		columns, rows = run_report("User Role Listing", {})
		self.assertTrue(any("Administrator" in str(row) for row in rows))
