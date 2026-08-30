import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from csf_tz.custom_api import (
	get_item_info,
	get_item_prices,
	get_item_prices_custom,
	get_item_prices_custom_po,
	get_item_prices_po,
	get_pending_sales_invoice,
	get_warehouse_options,
)
from csf_tz.tests.custom_api_helpers import (
	COMPANY,
	CUSTOMER,
	SUPPLIER,
	WAREHOUSE,
	disable_db_commit_for_class,
	make_purchase_invoice,
	make_sales_invoice,
	make_test_item,
	set_csf_settings,
)


class TestItemPrices(IntegrationTestCase):
	"""Price history dialogs read submitted Sales and Purchase Invoices."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		disable_db_commit_for_class(cls)
		cls.item = make_test_item("_CSF Price History Item")
		for rate in (100, 100, 120):
			make_sales_invoice(item_code=cls.item.name, rate=rate)
		for rate in (50, 50, 60):
			make_purchase_invoice(item_code=cls.item.name, rate=rate)

	def setUp(self):
		set_csf_settings(unique_records=0)

	def test_sales_prices_all_records(self):
		rows = get_item_prices(self.item.name, "INR", CUSTOMER, COMPANY)
		self.assertEqual(sorted(row["price"] for row in rows), [100, 100, 120])
		self.assertEqual({row["customer"] for row in rows}, {CUSTOMER})

	def test_sales_prices_unique(self):
		set_csf_settings(unique_records=1)
		rows = get_item_prices(self.item.name, "INR", None, COMPANY)
		self.assertEqual(sorted(row["price"] for row in rows), [100, 120])

	def test_sales_prices_custom_filters(self):
		filters = {
			"item_code": self.item.name,
			"currency": "INR",
			"customer": CUSTOMER,
			"company": COMPANY,
			"posting_date": ["Between", [nowdate(), nowdate()]],
		}
		rows = get_item_prices_custom(json.dumps(filters), 0, 20)
		self.assertEqual(sorted(row["rate"] for row in rows), [100, 100, 120])
		self.assertEqual({row["posting_date"] for row in rows}, {frappe.utils.getdate(nowdate())})
		self.assertEqual(get_item_prices_custom(None), [])

	def test_sales_prices_custom_invalid_json(self):
		self.assertRaises(frappe.ValidationError, get_item_prices_custom, "{not json")

	def test_purchase_prices(self):
		rows = get_item_prices_po(self.item.name, "INR", SUPPLIER, COMPANY)
		self.assertEqual(sorted(row["price"] for row in rows), [50, 50, 60])
		set_csf_settings(unique_records=1)
		filters = {"item_code": self.item.name, "currency": "INR", "customer": SUPPLIER, "company": COMPANY}
		rows = get_item_prices_custom_po(filters)
		self.assertEqual(sorted(row["rate"] for row in rows), [50, 60])

	def test_warehouse_options(self):
		warehouses = get_warehouse_options(COMPANY)
		self.assertIn(WAREHOUSE, warehouses)
		self.assertNotIn("All Warehouses - _TC", warehouses)

	def test_item_info_for_plain_item(self):
		rows = get_item_info("_Test Item")
		self.assertIn((WAREHOUSE, ""), [(row["warehouse"], row["batch_no"]) for row in rows])
		self.assertEqual(get_item_info(self.item.name), [])

	def test_guest_cannot_read_price_history(self):
		with self.set_user("Guest"):
			self.assertRaises(frappe.PermissionError, get_item_prices, self.item.name, "INR", None, COMPANY)
			self.assertRaises(frappe.PermissionError, get_item_prices_custom, {"item_code": self.item.name})
			self.assertRaises(
				frappe.PermissionError, get_item_prices_po, self.item.name, "INR", None, COMPANY
			)
			self.assertRaises(frappe.PermissionError, get_item_prices_custom_po, {})
			self.assertRaises(frappe.PermissionError, get_item_info, self.item.name)
			self.assertRaises(
				frappe.PermissionError, get_pending_sales_invoice, "Sales Invoice", "", "name", 0, 20, {}
			)
