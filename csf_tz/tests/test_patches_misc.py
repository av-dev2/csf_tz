from unittest.mock import patch

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe.tests import IntegrationTestCase

from csf_tz.patches import (
	add_custom_field_for_cusomer_suppliers_groups,
	add_custom_fields_for_employee_advance,
	add_custom_fields_for_sales_invoice_item_and_purchase_invoice_item,
	add_custom_fields_on_customer_for_auto_close_dn,
	create_the_stock_entry_type,
	delete_default_value_fields,
	disable_signup_in_website_settings,
	fix_module_for_core_reports,
	remove_deleted_modules_metadata,
	remove_education_doctypes,
	remove_ot_component_custom_fields,
	remove_stock_entry_qty_field,
	update_payware_settings_values_to_csf_tz_settings,
	update_salary_slips_from_currrent_employee_payroll_cost_center,
)
from csf_tz.patches.custom_fields import (
	attendance_overtime_calculation_custom_fields,
	create_custom_fields_for_additional_salary,
	custom_fields_for_removed_edu_fields_in_csf_tz,
	delete_employee_custom_fields,
	payroll_approval_custom_fields,
	payroll_cost_center_custom_fields,
)
from csf_tz.tests.integration_fixtures import make_payroll_entry_stub, make_salary_slip_stub

CUSTOM_FIELD_PATCHES = {
	custom_fields_for_removed_edu_fields_in_csf_tz: ["Account-item", "Address-tax_category"],
	add_custom_fields_for_sales_invoice_item_and_purchase_invoice_item: [
		"Sales Invoice Item-csf_tz_wtax_jv_created",
		"Purchase Invoice Item-csf_tz_create_wtax_entry",
	],
	add_custom_fields_on_customer_for_auto_close_dn: [
		"Customer-csf_tz_is_auto_close_dn",
		"Customer-csf_tz_close_dn_after",
	],
	create_custom_fields_for_additional_salary: [
		"Additional Salary-based_on_hourly_rate",
		"Additional Salary-auto_created_based_on",
	],
	payroll_approval_custom_fields: [
		"Payroll Entry-has_payroll_approval",
		"Salary Slip-has_payroll_approval",
	],
	attendance_overtime_calculation_custom_fields: [
		"Employee-overtime_applicable",
		"Attendance-overtime_applicable",
	],
	add_custom_fields_for_employee_advance: ["Employee Advance-from_date", "Employee Advance-to_date"],
	add_custom_field_for_cusomer_suppliers_groups: [
		"Customer Group-tax_category",
		"Supplier Group-tax_category",
	],
	payroll_cost_center_custom_fields: ["Salary Slip-payroll_cost_center"],
}


class TestCustomFieldPatches(IntegrationTestCase):
	def test_custom_field_patches_are_idempotent(self):
		for module, expected_fields in CUSTOM_FIELD_PATCHES.items():
			with self.subTest(patch=module.__name__):
				module.execute()
				count = frappe.db.count("Custom Field")
				module.execute()
				self.assertEqual(frappe.db.count("Custom Field"), count)
				for name in expected_fields:
					self.assertTrue(frappe.db.exists("Custom Field", name), name)

	def test_delete_employee_custom_fields(self):
		with patch.object(frappe.db, "commit"):
			delete_employee_custom_fields.execute()
			delete_employee_custom_fields.execute()
		self.assertFalse(frappe.db.get_value("Custom Field", "Employee-bank_country_code", "options"))

	def test_remove_ot_component_custom_fields(self):
		create_custom_field(
			"Employee", {"fieldname": "overtime_components", "fieldtype": "Data", "label": "OT Components"}
		)
		self.assertTrue(frappe.db.exists("Custom Field", "Employee-overtime_components"))
		remove_ot_component_custom_fields.execute()
		remove_ot_component_custom_fields.execute()
		self.assertFalse(frappe.db.exists("Custom Field", "Employee-overtime_components"))
		self.assertFalse(frappe.db.exists("DocType", "Employee OT Component"))

	def test_remove_stock_entry_qty_field(self):
		remove_stock_entry_qty_field.execute()
		remove_stock_entry_qty_field.execute()
		self.assertFalse(frappe.db.exists("Custom Field", "Stock Entry-qty"))
		self.assertNotIn("qty", frappe.db.get_table_columns("Stock Entry"))


class TestDataPatches(IntegrationTestCase):
	def test_create_the_stock_entry_type_is_idempotent(self):
		create_the_stock_entry_type.execute()
		create_the_stock_entry_type.execute()
		self.assertEqual(frappe.db.get_value("Stock Entry Type", "To Company", "purpose"), "Material Receipt")
		self.assertEqual(frappe.db.get_value("Stock Entry Type", "From Company", "purpose"), "Material Issue")
		self.assertEqual(
			frappe.db.count("Stock Entry Type", {"name": ["in", ["To Company", "From Company"]]}), 2
		)

	def test_fix_module_for_core_reports(self):
		frappe.db.set_value("Report", "Stock Ageing", "module", "Accounts", update_modified=False)
		with patch.dict(frappe.flags, {"in_patch": True}):
			fix_module_for_core_reports.execute()
			fix_module_for_core_reports.execute()
		self.assertEqual(frappe.db.get_value("Report", "Stock Ageing", "module"), "Stock")
		self.assertEqual(frappe.db.get_value("Report", "Total Stock Summary", "module"), "Stock")

	def test_delete_default_value_fields(self):
		frappe.defaults.set_global_default("year_start_date", "2026-01-01")
		self.assertTrue(frappe.db.exists("DefaultValue", {"defkey": "year_start_date"}))
		delete_default_value_fields.execute()
		delete_default_value_fields.execute()
		self.assertFalse(frappe.db.exists("DefaultValue", {"defkey": "year_start_date"}))

	def test_disable_signup_in_website_settings(self):
		frappe.db.set_single_value("Website Settings", "disable_signup", 0)
		disable_signup_in_website_settings.execute()
		disable_signup_in_website_settings.execute()
		self.assertEqual(frappe.db.get_single_value("Website Settings", "disable_signup"), 1)

	def test_update_salary_slips_from_employee_payroll_cost_center(self):
		payroll_cost_center_custom_fields.execute()
		payroll_entry = make_payroll_entry_stub()
		slip = make_salary_slip_stub(payroll_entry, "_T-Employee-00001", 100)
		frappe.db.set_value("Employee", "_T-Employee-00001", "payroll_cost_center", "_Test Cost Center - _TC")
		frappe.db.set_value("Salary Slip", slip.name, "payroll_cost_center", None)

		update_salary_slips_from_currrent_employee_payroll_cost_center.execute()
		update_salary_slips_from_currrent_employee_payroll_cost_center.execute()
		self.assertEqual(
			frappe.db.get_value("Salary Slip", slip.name, "payroll_cost_center"), "_Test Cost Center - _TC"
		)

	def test_update_payware_settings_skips_without_payware(self):
		self.assertFalse(frappe.db.exists("DocType", "Payware Settings"))
		with patch("frappe.get_doc") as get_doc:
			update_payware_settings_values_to_csf_tz_settings.execute()
		get_doc.assert_not_called()

	def test_inline_report_deletion_patch(self):
		frappe.delete_doc_if_exists("Report", "Stock Ledger Mismatch")
		self.assertFalse(frappe.db.exists("Report", "Stock Ledger Mismatch"))


class TestModulePatches(IntegrationTestCase):
	def test_remove_education_doctypes(self):
		remove_education_doctypes.execute()
		with (
			patch("frappe.get_installed_apps", return_value=["frappe", "edu_tz"]),
			patch("frappe.delete_doc") as delete_doc,
		):
			remove_education_doctypes.execute()
		delete_doc.assert_not_called()
		with (
			patch("frappe.db.get_value", return_value="CSF TZ"),
			patch("frappe.db.count", return_value=1),
			patch("frappe.delete_doc") as delete_doc,
		):
			remove_education_doctypes.execute()
		delete_doc.assert_not_called()
		with (
			patch("frappe.db.get_value", return_value="CSF TZ"),
			patch("frappe.db.count", return_value=0),
			patch("frappe.delete_doc") as delete_doc,
		):
			remove_education_doctypes.execute()
		self.assertEqual(delete_doc.call_count, 2)

	def test_remove_deleted_modules_metadata(self):
		remove_deleted_modules_metadata.execute()
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": "Fleet Management", "app_name": "csf_tz"}
		).insert()
		remove_deleted_modules_metadata.execute()
		self.assertFalse(frappe.db.exists("Module Def", "Fleet Management"))
		remove_deleted_modules_metadata.execute()

	def test_remove_doctype_metadata_without_table(self):
		with patch.object(frappe.db, "sql_ddl") as sql_ddl:
			remove_deleted_modules_metadata.remove_doctype_metadata("No Such CSF DocType")
		sql_ddl.assert_not_called()
		remove_deleted_modules_metadata.remove_module_records(set())
		remove_deleted_modules_metadata.remove_doctypes(set())
