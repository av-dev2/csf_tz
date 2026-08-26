import json

from frappe.tests import IntegrationTestCase

from csf_tz.csftz_hooks import get_relation_json, get_successor_json


class TestRelationJson(IntegrationTestCase):
	def test_relation_json_lists_link_targets_per_doctype(self):
		doc_list = json.loads(get_relation_json.get_json())
		sales_invoice = next(row for row in doc_list if row["doctype_name"] == "Sales Invoice")
		self.assertEqual(sales_invoice["name"], "erpnext.Accounts.Sales Invoice")
		self.assertIn("erpnext.Accounts.Customer", sales_invoice["imports"])
		self.assertIn("erpnext.Accounts.Company", sales_invoice["imports"])

	def test_successor_json_is_limited_to_ancestor_module(self):
		doc_list = json.loads(get_successor_json.get_json("Sales Invoice", "Accounts"))
		names = {row["doctype_name"] for row in doc_list}
		self.assertIn("Sales Invoice", names)
		self.assertIn("Payment Entry", names)
		self.assertNotIn("Stock Entry", names)
		self.assertTrue(all(row["name"].startswith("erpnext.Accounts.") for row in doc_list))

	def test_successor_json_default_module_is_accounts(self):
		default = json.loads(get_successor_json.get_json("Sales Invoice"))
		explicit = json.loads(get_successor_json.get_json("Sales Invoice", "Accounts"))
		self.assertEqual(default, explicit)

	def test_successor_json_for_stock_module(self):
		names = {row["doctype_name"] for row in json.loads(get_successor_json.get_json("Item", "Stock"))}
		self.assertIn("Stock Entry", names)
		self.assertNotIn("Sales Invoice", names)
