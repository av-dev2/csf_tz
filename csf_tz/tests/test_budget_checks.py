from unittest.mock import patch

import frappe
from erpnext.accounts.doctype.budget.budget import BudgetError
from erpnext.accounts.doctype.budget.test_budget import make_budget
from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from erpnext.buying.doctype.purchase_order.test_purchase_order import create_purchase_order
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from csf_tz.budget_check import (
	check_budget_before_submit,
	check_budget_for_buying_document,
	check_budget_for_journal_entry,
	is_budget_check_enabled,
	validate_budget_on_draft,
)
from csf_tz.csftz_hooks.budget import check_budget_for_purchase_order
from csf_tz.tests.import_fixtures import COMPANY

EXPENSE_ACCOUNT = "_Test Account Cost for Goods Sold - _TC"
COST_CENTER = "_Test Cost Center - _TC"
CASH_ACCOUNT = "Cash - _TC"
BUDGET_AMOUNT = 100000
OVER_BUDGET = BUDGET_AMOUNT * 2


class TestBudgetChecks(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_budget(
			budget_against="Cost Center",
			cost_center=COST_CENTER,
			budget_amount=BUDGET_AMOUNT,
			applicable_on_material_request=1,
			action_if_annual_budget_exceeded_on_mr="Stop",
			applicable_on_purchase_order=1,
			action_if_annual_budget_exceeded_on_po="Stop",
			submit_budget=1,
		)

	def enable(self, field):
		frappe.db.set_single_value("CSF TZ Settings", field, 1)
		self.addCleanup(frappe.db.set_single_value, "CSF TZ Settings", field, 0)

	def make_material_request(self, rate, qty=1):
		request = frappe.new_doc("Material Request")
		request.update({"material_request_type": "Purchase", "company": COMPANY, "schedule_date": nowdate()})
		request.append(
			"items",
			{
				"item_code": "_Test Item",
				"qty": qty,
				"rate": rate,
				"amount": qty * rate,
				"uom": "_Test UOM",
				"conversion_factor": 1,
				"schedule_date": nowdate(),
				"warehouse": "_Test Warehouse - _TC",
				"cost_center": COST_CENTER,
				"expense_account": EXPENSE_ACCOUNT,
			},
		)
		request.insert()
		return request

	def test_purchase_invoice_over_budget_is_blocked(self):
		self.enable("check_budget_in_pi")
		with self.assertRaises(BudgetError):
			make_purchase_invoice(rate=OVER_BUDGET, qty=1, do_not_submit=True)

	def test_purchase_invoice_within_budget_saves(self):
		self.enable("check_budget_in_pi")
		invoice = make_purchase_invoice(rate=10, qty=1, do_not_submit=True)
		self.assertTrue(invoice.name)

	def test_purchase_invoice_check_is_off_by_default(self):
		invoice = make_purchase_invoice(rate=OVER_BUDGET, qty=1, do_not_submit=True)
		self.assertTrue(invoice.name)

	def test_material_request_over_budget_is_blocked(self):
		self.enable("check_budget_in_mr")
		with self.assertRaises(BudgetError):
			self.make_material_request(OVER_BUDGET)
		self.assertTrue(self.make_material_request(10).name)

	def test_journal_entry_over_budget_is_blocked(self):
		self.enable("check_budget_in_je")
		with self.assertRaises(BudgetError):
			make_journal_entry(EXPENSE_ACCOUNT, CASH_ACCOUNT, OVER_BUDGET, cost_center=COST_CENTER)
		credit_note = make_journal_entry(CASH_ACCOUNT, EXPENSE_ACCOUNT, OVER_BUDGET, cost_center=COST_CENTER)
		self.assertTrue(credit_note.name)

	def test_purchase_order_over_budget_is_blocked(self):
		order = create_purchase_order(rate=OVER_BUDGET, qty=1, do_not_submit=True)
		self.enable("check_budget_in_po")
		with self.assertRaises(BudgetError):
			check_budget_for_purchase_order(order)
		with self.assertRaises(BudgetError):
			order.save()
		frappe.db.set_single_value("CSF TZ Settings", "check_budget_in_po", 0)
		self.assertIsNone(check_budget_for_purchase_order(order))

	def test_is_budget_check_enabled(self):
		self.assertFalse(is_budget_check_enabled("Sales Invoice"))
		self.assertFalse(is_budget_check_enabled("Journal Entry"))

	def test_validate_budget_on_draft(self):
		journal_entry = make_journal_entry(
			EXPENSE_ACCOUNT, CASH_ACCOUNT, OVER_BUDGET, cost_center=COST_CENTER
		)
		self.assertIsNone(validate_budget_on_draft(journal_entry))
		with patch("csf_tz.budget_check.is_budget_check_enabled", return_value=True):
			with self.assertRaises(BudgetError):
				validate_budget_on_draft(journal_entry)
			journal_entry.docstatus = 1
			self.assertIsNone(validate_budget_on_draft(journal_entry))

	def test_check_budget_before_submit(self):
		journal_entry = make_journal_entry(
			EXPENSE_ACCOUNT, CASH_ACCOUNT, OVER_BUDGET, cost_center=COST_CENTER
		)
		self.assertIsNone(check_budget_before_submit("Journal Entry", journal_entry.name))
		self.enable("check_budget_in_je")
		with self.assertRaises(BudgetError):
			check_budget_before_submit(
				"Journal Entry", journal_entry.name, setting_field="check_budget_in_je"
			)
		self.assertIsNone(check_budget_before_submit(None, None))
		self.assertIsNone(check_budget_before_submit("Sales Invoice", journal_entry.name))
		self.assertIsNone(
			check_budget_before_submit("Journal Entry", "MISSING", setting_field="check_budget_in_je")
		)

	def test_check_budget_for_documents_directly(self):
		with self.assertRaises(BudgetError):
			check_budget_for_journal_entry(
				make_journal_entry(EXPENSE_ACCOUNT, CASH_ACCOUNT, OVER_BUDGET, cost_center=COST_CENTER)
			)
		with self.assertRaises(BudgetError):
			check_budget_for_buying_document(self.make_material_request(OVER_BUDGET))
		with self.assertRaises(BudgetError):
			check_budget_for_buying_document(
				make_purchase_invoice(rate=OVER_BUDGET, qty=1, do_not_submit=True)
			)
		with self.assertRaises(BudgetError):
			check_budget_for_buying_document(
				create_purchase_order(rate=OVER_BUDGET, qty=1, do_not_submit=True)
			)
		self.assertIsNone(
			check_budget_for_buying_document(make_purchase_invoice(rate=10, qty=1, do_not_submit=True))
		)
