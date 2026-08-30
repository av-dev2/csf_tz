import frappe
from erpnext.buying.doctype.purchase_order.test_purchase_order import create_purchase_order
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.material_request.test_material_request import make_material_request
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from csf_tz.tests.report_fixtures import (
	COMPANY,
	ITEM,
	WAREHOUSE,
	as_dicts,
	date_range,
	fieldnames,
	receive_stock,
	run_report,
	sell_stock,
)


class TestStockReports(IntegrationTestCase):
	"""Runs the stock and procurement reports of csf_tz against fresh stock movements."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.stock_entry = receive_stock(qty=30, rate=100)
		cls.sales_invoice = sell_stock(qty=2, rate=500)
		cls.sales_order = make_sales_order(qty=4, rate=500)
		cls.purchase_order = create_purchase_order(qty=6, rate=50)
		cls.material_request = make_material_request(qty=3)

	def stock_filters(self, **extra):
		return {"company": COMPANY, **date_range(), **extra}

	def item_row(self, columns, rows, key="item_code"):
		return [r for r in as_dicts(columns, rows) if r.get(key) == ITEM]

	def test_csf_tz_stock_movement(self):
		columns, rows = run_report("CSF TZ Stock Movement", self.stock_filters())
		self.assertTrue(columns)
		self.assertTrue(rows)

	def test_itemwise_stock_movement(self):
		columns, rows = run_report("Itemwise Stock Movement", self.stock_filters(warehouse=WAREHOUSE))
		self.assertTrue(columns)
		self.assertTrue(rows)

	def test_stock_balance_pro(self):
		columns, rows = run_report("Stock Balance Pro", self.stock_filters())
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_stock_balance_pivot_warehouse(self):
		columns, rows = run_report(
			"Stock Balance pivot warehouse", {**date_range(), "filter_total_zero_qty": 1}
		)
		self.assertIn(WAREHOUSE, [c["label"] for c in columns])
		rows = self.item_row(columns, rows)
		self.assertEqual(len(rows), 1, rows)
		self.assertEqual(
			rows[0]["total_stock"], sum(v for k, v in rows[0].items() if k.startswith("_test_warehouse"))
		)

	def test_warehouse_wise_item_balance_and_value(self):
		columns, rows = run_report(
			"Warehouse wise Item Balance and Value",
			{**date_range(), "company": COMPANY, "warehouse": WAREHOUSE, "filter_total_zero_qty": 1},
		)
		self.assertEqual(fieldnames(columns)[:4], ["item", "item_group", "value", "age"])
		rows = self.item_row(columns, rows, key="item")
		self.assertEqual(len(rows), 1, rows)
		self.assertGreater(rows[0]["value"], 0)

	def test_particular_item_history_report(self):
		columns, rows = run_report("Particular Item History Report", {**date_range(), "warehouse": WAREHOUSE})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_item_price_by_price_list(self):
		columns, rows = run_report("Item Price by Price List", {"tax_rate": 18})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_balance_below_safety_stock(self):
		columns, rows = run_report("Balance below Safety Stock", {})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_stock_ledger_for_trading_account(self):
		for report_name in ("Stock Ledger for Trading Account", "Stock Ledger Summary for Trading Account"):
			columns, rows = run_report(report_name, date_range())
			self.assertTrue(columns, report_name)
			self.assertTrue(rows, report_name)

	def test_stock_reconciliation_troubleshoot(self):
		columns, rows = run_report("Stock Reconciliation troubleshoot", date_range())
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_bin_system_report_builder(self):
		report = frappe.get_doc("Report", "Bin System")
		self.assertEqual((report.report_type, report.ref_doctype), ("Report Builder", "Bin"))
		rows = frappe.get_list("Bin", filters={"item_code": ITEM}, fields=["warehouse", "actual_qty"])
		self.assertTrue(any(r["warehouse"] == WAREHOUSE for r in rows))

	def test_ordered_items_to_be_delivered(self):
		columns, rows = run_report("Ordered Items To Be Delivered", date_range())
		self.assertTrue(any(self.sales_order.name in str(row) for row in rows))

	def test_pending_ordered_items(self):
		columns, rows = run_report("Pending Ordered Items", date_range())
		self.assertTrue(any(self.purchase_order.name in str(row) for row in rows))

	def test_purchase_history(self):
		columns, rows = run_report("Purchase History", date_range())
		self.assertTrue(any(self.purchase_order.name in str(row) for row in rows))

	def test_reordering_items(self):
		columns, rows = run_report("Reordering Items", date_range())
		self.assertTrue(any(self.material_request.name in str(row) for row in rows))

	def test_shipment_tracking(self):
		order = frappe.get_doc(
			{
				"doctype": "Order Track",
				"supplier": "_Test Supplier",
				"supplier_type": "International Supplier",
				"expected_arrival_date": add_days(today(), 5),
				"shipped_date": today(),
				"bl_number": "BL-1",
			}
		).insert()
		columns, rows = run_report(
			"Shipment Tracking", {**date_range(), "supplier": "_Test Supplier", "order": order.name}
		)
		rows = as_dicts(columns, rows)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["order_no"], order.name)
		self.assertEqual(rows[0]["bl_number"], "BL-1")

	def test_supplier_contacts(self):
		columns, rows = run_report("Supplier Contacts", {"party_type": "Supplier"})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)
