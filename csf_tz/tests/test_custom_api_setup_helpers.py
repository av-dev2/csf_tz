import frappe
from frappe.tests import IntegrationTestCase

from csf_tz.custom_api import (
	account_exists,
	auto_create_account,
	create_item_tax_template,
	create_tax_category,
	get_tax_category,
	linking_tax_template,
	make_salary_components_and_structure,
)
from csf_tz.tests.custom_api_helpers import COMPANY, make_test_item, set_csf_settings


def make_tax_template(doctype, title, tax_category):
	account = frappe.get_all(
		"Account", filters={"company": COMPANY, "account_type": "Tax", "is_group": 0}, pluck="name", limit=1
	)[0]
	return frappe.get_doc(
		doctype=doctype,
		title=title,
		company=COMPANY,
		is_default=1,
		tax_category=tax_category,
		taxes=[{"charge_type": "On Net Total", "account_head": account, "description": title, "rate": 5}],
	).insert()


class TestGetTaxCategory(IntegrationTestCase):
	"""Default tax category comes from the company's default tax template."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_tax_template("Sales Taxes and Charges Template", "_CSF Default Sales", "_Test Tax Category 1")
		make_tax_template(
			"Purchase Taxes and Charges Template", "_CSF Default Purchase", "_Test Tax Category 2"
		)

	def setUp(self):
		set_csf_settings(fetch_default_tax_category=1)

	def test_sales_and_purchase_documents(self):
		self.assertEqual(get_tax_category("Sales Invoice", COMPANY), "_Test Tax Category 1")
		self.assertEqual(get_tax_category("Purchase Order", COMPANY), "_Test Tax Category 2")
		self.assertEqual(get_tax_category("Journal Entry", COMPANY), "")

	def test_disabled_setting_returns_empty(self):
		set_csf_settings(fetch_default_tax_category=0)
		self.assertEqual(get_tax_category("Sales Invoice", COMPANY), "")


class TestTanzaniaSetup(IntegrationTestCase):
	"""Setup helpers create accounts, tax templates, categories and payroll defaults for a company."""

	def test_accounts_and_tax_templates(self):
		self.assertFalse(account_exists("NSSF Payable"))
		self.assertEqual(auto_create_account("_TC"), "Account added successfully.")
		self.assertTrue(account_exists("NSSF Payable"))
		self.assertEqual(
			frappe.db.get_value("Account", "NSSF Payable - _TC", "parent_account"),
			"Payroll Liabilities - _TC",
		)
		self.assertEqual(
			frappe.db.get_value("Account", "Salary Expense - _TC", "account_type"), "Expense Account"
		)
		self.assertEqual(auto_create_account("_TC"), "Account added successfully.")

		self.assertEqual(create_item_tax_template("_TC"), "Tax Template added successfully.")
		template = frappe.get_doc("Item Tax Template", "Tanzania VAT 18% - _TC")
		self.assertEqual(template.taxes[0].tax_type, "OUTPUT VAT - 18% - _TC")
		self.assertTrue(frappe.db.exists("Item Tax Template", "Zanzibar VAT Tax 0% - _TC"))
		self.assertEqual(create_item_tax_template("_TC"), "Tax Template added successfully.")

		self.assertEqual(create_tax_category(), "Tax Categories added successfully.")
		self.assertTrue(frappe.db.exists("Tax Category", "Non Taxable"))
		self.assertEqual(create_tax_category(), "Tax Categories added successfully.")

		item = make_test_item("_CSF Tax Link Item")
		frappe.db.set_value("Item", item.name, "default_tax_template", "Tanzania VAT 18% - _TC")
		filters = {"default_tax_template": "Tanzania VAT 18% - _TC"}
		self.assertEqual(
			linking_tax_template("Item", filters, "_TC"), "Item Tax Template Linked successfully."
		)
		taxes = [(row.item_tax_template, row.tax_category) for row in frappe.get_doc("Item", item.name).taxes]
		self.assertEqual(
			taxes, [("Tanzania VAT 18% - _TC", "Sales"), ("Tanzania Purchase VAT 18% - _TC", "Purchase")]
		)

	def test_salary_components_and_structure(self):
		auto_create_account("_TC")
		self.assertEqual(
			make_salary_components_and_structure("_TC"),
			"Salary Components and Structure are created successfully.",
		)
		component = frappe.get_doc("Salary Component", "NSSF Employee")
		self.assertEqual(component.accounts[0].account, "NSSF Payable - _TC")
		structure = frappe.get_doc("Salary Structure", "Tanzania Mainland")
		self.assertEqual(structure.docstatus, 1)
		self.assertIn("PAYE Payable", [row.salary_component for row in structure.deductions])
		self.assertIsNone(make_salary_components_and_structure("_TC"))

	def test_unknown_abbreviation_throws(self):
		self.assertRaisesRegex(frappe.ValidationError, "No Company", create_item_tax_template, "_NOPE")
