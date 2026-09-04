from unittest.mock import patch

import frappe
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.material_request.material_request import (
	make_stock_entry as make_entry_from_request,
)
from erpnext.stock.doctype.material_request.test_material_request import make_material_request
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.tests import IntegrationTestCase

from csf_tz.csftz_hooks.stock import (
	import_from_bom,
	validate_with_material_request,
	validate_with_material_request_override,
)

COMPANY = "_Test Company"
WAREHOUSE = "_Test Warehouse - _TC"
TARGET_WAREHOUSE = "_Test Warehouse 1 - _TC"
EXPENSE_ACCOUNT = "Expenses Included In Valuation - _TC"


def make_bom(finished_item, raw_item):
	bom = frappe.new_doc("BOM")
	bom.item = finished_item
	bom.company = COMPANY
	bom.quantity = 1
	bom.append("items", {"item_code": raw_item, "qty": 2, "rate": 50})
	bom.insert()
	bom.submit()
	return bom


def bom_with_costs(bom, rows):
	real_get_doc = frappe.get_doc

	def get_doc(*args, **kwargs):
		if args[:2] == ("BOM", bom.name):
			bom.additional_costs = rows
			return bom
		return real_get_doc(*args, **kwargs)

	return patch.object(frappe, "get_doc", side_effect=get_doc)


class TestImportFromBom(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.raw_item = make_item(properties={"is_stock_item": 1, "valuation_rate": 50}).name
		cls.finished_item = make_item(properties={"is_stock_item": 1}).name
		cls.bom = make_bom(cls.finished_item, cls.raw_item)
		make_stock_entry(item_code=cls.raw_item, qty=50, to_warehouse=WAREHOUSE, rate=50, company=COMPANY)

	def make_manufacture_entry(self):
		entry = frappe.new_doc("Stock Entry")
		entry.company = COMPANY
		entry.stock_entry_type = "Manufacture"
		entry.purpose = "Manufacture"
		entry.from_bom = 1
		entry.bom_no = self.bom.name
		entry.fg_completed_qty = 1
		entry.from_warehouse = WAREHOUSE
		entry.to_warehouse = TARGET_WAREHOUSE
		entry.get_items()
		return entry

	def test_bom_without_cost_table_leaves_entry_untouched(self):
		entry = self.make_manufacture_entry()
		entry.save()
		self.assertEqual(entry.additional_costs, [])
		self.assertEqual(entry.total_additional_costs, 0)

	def test_bom_costs_are_copied_and_distributed(self):
		rows = [
			frappe._dict(expense_account=EXPENSE_ACCOUNT, cost_per_unit=30, cost_type="Labour"),
			frappe._dict(expense_account=EXPENSE_ACCOUNT, cost_per_unit=20, cost_type="Power"),
		]
		entry = self.make_manufacture_entry()
		with bom_with_costs(self.bom, rows):
			entry.save()
		self.assertEqual(
			[
				(row.expense_account, row.amount, row.base_amount, row.description)
				for row in entry.additional_costs
			],
			[(EXPENSE_ACCOUNT, 30, 30, "Labour"), (EXPENSE_ACCOUNT, 20, 20, "Power")],
		)
		entry.submit()
		self.assertEqual(entry.total_additional_costs, 50)
		finished_row = next(row for row in entry.items if row.is_finished_item)
		self.assertEqual(finished_row.additional_cost, 50)

	def test_saving_again_does_not_duplicate_costs(self):
		rows = [frappe._dict(expense_account=EXPENSE_ACCOUNT, cost_per_unit=30, cost_type="Labour")]
		entry = self.make_manufacture_entry()
		with bom_with_costs(self.bom, rows):
			entry.save()
			entry.remarks = "saved twice"
			entry.save()
		self.assertEqual(len(entry.additional_costs), 1)

	def test_other_entry_types_do_not_read_the_bom(self):
		entry = make_stock_entry(
			item_code=self.raw_item, qty=1, to_warehouse=WAREHOUSE, rate=50, company=COMPANY, do_not_save=True
		)
		entry.bom_no = self.bom.name
		with patch.object(frappe, "get_doc", side_effect=AssertionError("BOM must not be loaded")):
			import_from_bom(entry, "before_save")
		self.assertEqual(entry.additional_costs, [])


class TestValidateWithMaterialRequest(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_item(properties={"is_stock_item": 1, "valuation_rate": 10}).name
		make_stock_entry(item_code=cls.item, qty=20, to_warehouse=WAREHOUSE, rate=10, company=COMPANY)
		cls.request = make_material_request(
			material_request_type="Material Transfer",
			item_code=cls.item,
			qty=5,
			uom="Nos",
			from_warehouse=WAREHOUSE,
			warehouse=TARGET_WAREHOUSE,
		)

	def make_transfer_entry(self):
		entry = make_entry_from_request(self.request.name)
		entry.stock_entry_type = "Material Transfer"
		return entry

	def test_matching_item_and_warehouse_pass(self):
		entry = self.make_transfer_entry()
		self.assertEqual(entry.items[0].material_request, self.request.name)
		validate_with_material_request(entry)

	def test_item_mismatch_throws(self):
		entry = self.make_transfer_entry()
		entry.items[0].item_code = "_Test Item"
		with self.assertRaises(frappe.MappingMismatchError):
			validate_with_material_request(entry)

	def test_target_warehouse_mismatch_throws(self):
		entry = self.make_transfer_entry()
		entry.items[0].t_warehouse = WAREHOUSE
		with self.assertRaises(frappe.MappingMismatchError):
			validate_with_material_request(entry)

	def test_material_issue_compares_source_warehouse(self):
		entry = self.make_transfer_entry()
		entry.purpose = "Material Issue"
		entry.items[0].s_warehouse = TARGET_WAREHOUSE
		validate_with_material_request(entry)
		entry.items[0].s_warehouse = WAREHOUSE
		with self.assertRaises(frappe.MappingMismatchError):
			validate_with_material_request(entry)

	def test_company_bypass_skips_validation(self):
		frappe.db.set_value("Company", COMPANY, "bypass_material_request_validation", 1)
		self.addCleanup(frappe.db.set_value, "Company", COMPANY, "bypass_material_request_validation", 0)
		entry = self.make_transfer_entry()
		entry.items[0].item_code = "_Test Item"
		validate_with_material_request(entry)

	def test_override_replaces_erpnext_method(self):
		original = StockEntry.validate_with_material_request
		self.addCleanup(setattr, StockEntry, "validate_with_material_request", original)
		validate_with_material_request_override(None, "before_validate")
		self.assertIs(StockEntry.validate_with_material_request, validate_with_material_request)
