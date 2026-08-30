import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from csf_tz.utils import create_custom_fields as custom_fields_util
from csf_tz.utils import create_property_setter as property_setter_util
from csf_tz.utils import setup

TZ_COMPANY = "_Test TZ Setup Company"
TZ_ABBR = "_TTZ"


def make_tz_company():
	if frappe.db.exists("Company", TZ_COMPANY):
		return TZ_COMPANY
	frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": TZ_COMPANY,
			"abbr": TZ_ABBR,
			"default_currency": "TZS",
			"country": "Tanzania",
			"chart_of_accounts": "Standard",
		}
	).insert()
	return TZ_COMPANY


def count_records():
	return {spec["doctype"]: frappe.db.count(spec["doctype"]) for spec in setup.SETUP_SPECS}


class TestSetupExecute(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = make_tz_company()

	def test_execute_skips_without_company_or_non_tzs_company(self):
		with patch.object(setup, "get_target_company", return_value=None):
			setup.execute()
		with patch.object(setup, "get_target_company", return_value="_Test Company"):
			setup.execute()
		self.assertFalse(frappe.db.exists("Account", "Payroll Liabilities - _TC"))
		self.assertFalse(frappe.db.exists("Sales Taxes and Charges Template", "Standard (18%) - _TC"))

	def test_execute_creates_tz_defaults_idempotently(self):
		with patch.object(setup, "get_target_company", return_value=self.company):
			setup.execute()
			counts = count_records()
			setup.execute()
		self.assertEqual(count_records(), counts)

		self.assertTrue(frappe.db.exists("Account", f"Payroll Liabilities - {TZ_ABBR}"))
		self.assertEqual(
			frappe.db.get_value("Account", f"Input VAT - {TZ_ABBR}", ["account_type", "parent_account"]),
			("Tax", f"Tax Assets - {TZ_ABBR}"),
		)
		self.assertTrue(frappe.db.exists("Leave Type", "Annual Leave"))
		self.assertTrue(frappe.db.exists("Salary Component", "NSSF Employee"))
		self.assertTrue(frappe.db.exists("Sales Taxes and Charges Template", f"Standard (18%) - {TZ_ABBR}"))
		self.assertTrue(
			frappe.db.exists("Purchase Taxes and Charges Template", f"Standard (18%) - {TZ_ABBR}")
		)
		self.assertTrue(frappe.db.exists("Item Tax Template", f"Standard (18%) Sales Tax - {TZ_ABBR}"))
		self.assertEqual(
			frappe.db.get_value("Leave Policy", {"title": "Leave Policy For Men"}, "docstatus"),
			1,
		)
		self.assertEqual(frappe.db.get_value("Salary Structure", "TZ Standard", "company"), self.company)

	def test_get_target_company(self):
		with patch("frappe.db.get_single_value", return_value="_Test Company"):
			self.assertEqual(setup.get_target_company(), "_Test Company")
		with patch("frappe.db.get_single_value", return_value=None):
			self.assertEqual(
				setup.get_target_company(),
				frappe.get_all("Company", pluck="name", order_by="creation asc", limit=1)[0],
			)

	def test_render_and_resolve_helpers(self):
		record = {"a": "{company} - {abbr}", "rows": [{"parent_account": "Nested {abbr}"}], "n": 1}
		self.assertEqual(
			setup.render_record(record, "Co", "AB"),
			{"a": "Co - AB", "rows": [{"parent_account": "Nested AB"}], "n": 1},
		)
		self.assertEqual(setup.strip_company_suffix("Cash - _TC", "_TC"), "Cash")
		self.assertEqual(setup.strip_company_suffix("Cash", "_TC"), "Cash")

		self.assertEqual(setup.resolve_account("Cash - _TC", "_Test Company", "_TC"), "Cash - _TC")
		self.assertEqual(setup.resolve_account("Cash - XX", "_Test Company", "XX"), "Cash - _TC")
		self.assertEqual(
			setup.resolve_account("No Such Account - XX", "_Test Company", "XX"), "No Such Account - XX"
		)
		self.assertEqual(
			setup.resolve_cost_center("_Test Cost Center - XX", "_Test Company", "XX"),
			"_Test Cost Center - _TC",
		)
		self.assertEqual(setup.resolve_cost_center("Nope - XX", "_Test Company", "XX"), "Nope - XX")

		resolved = setup.resolve_links(
			{
				"account": "Cash - XX",
				"taxes": [{"account_head": "Cash - XX", "cost_center": "_Test Cost Center - XX"}],
			},
			"_Test Company",
			"XX",
		)
		self.assertEqual(resolved["account"], "Cash - _TC")
		self.assertEqual(
			resolved["taxes"][0], {"account_head": "Cash - _TC", "cost_center": "_Test Cost Center - _TC"}
		)

	def test_existing_name_and_submit_helpers(self):
		self.assertIsNone(setup.get_existing_name("Account", {"account_name": None}, ("account_name",)))
		self.assertEqual(
			setup.get_existing_name(
				"Account", {"account_name": "Cash", "company": "_Test Company"}, ("account_name", "company")
			),
			"Cash - _TC",
		)
		setup.submit_if_needed({"doctype": "Account"}, "Cash - _TC")
		setup.submit_if_needed({"doctype": "Leave Policy", "submit_after_insert": True}, "No Such Policy")
		self.assertEqual(len(setup.load_records("leave_types.json")), 6)
		with self.assertRaises(FileNotFoundError):
			setup.load_records("missing.json")


class TestCustomFieldAndPropertySetterUtils(IntegrationTestCase):
	def test_create_custom_fields_execute_is_idempotent(self):
		custom_fields_util.execute()
		count = frappe.db.count("Custom Field")
		custom_fields_util.execute()
		self.assertEqual(frappe.db.count("Custom Field"), count)
		self.assertTrue(frappe.db.exists("Custom Field", "Payroll Entry-cheque_number"))
		self.assertTrue(frappe.db.exists("Custom Field", "Employee-kcb_beneficiary_clearing_code"))

	def test_create_fields_from_json_skips_unknown_doctype(self):
		count = frappe.db.count("Custom Field")
		custom_fields_util.create_fields_from_json(
			[{"dt": "No Such DocType", "fieldname": "x", "fieldtype": "Data", "label": "X"}]
		)
		self.assertEqual(frappe.db.count("Custom Field"), count)
		self.assertEqual(
			custom_fields_util.load_json("16_payroll_entry_cheque.json")[1]["fieldname"], "cheque_number"
		)

	def test_export_custom_fields(self):
		exported = custom_fields_util.export_custom_fields(
			json.dumps(["Employee-kcb_beneficiary_clearing_code"])
		)
		self.assertIn("'fieldname': 'kcb_beneficiary_clearing_code'", exported)
		self.assertNotIn("'creation'", exported)

	def test_create_property_setter_execute_is_idempotent(self):
		property_setter_util.execute()
		count = frappe.db.count("Property Setter")
		property_setter_util.execute()
		self.assertEqual(frappe.db.count("Property Setter"), count)

	def test_create_property_setter_from_json(self):
		setters = [
			{
				"name": "Item-description-bold",
				"doc_type": "Item",
				"doctype_or_field": "DocField",
				"field_name": "description",
				"property": "bold",
				"property_type": "Check",
				"value": "1",
			},
			{
				"name": "No Such-x",
				"doc_type": "No Such DocType",
				"doctype_or_field": "DocType",
				"property": "track_changes",
				"property_type": "Check",
				"value": "1",
			},
		]
		property_setter_util.create_property_setter_from_json(setters)
		self.assertTrue(frappe.db.exists("Property Setter", "Item-description-bold"))
		count = frappe.db.count("Property Setter")
		property_setter_util.create_property_setter_from_json(setters)
		self.assertEqual(frappe.db.count("Property Setter"), count)
		self.assertTrue(property_setter_util.load_json("01_init.json"))
