import json
from datetime import date
from unittest.mock import ANY, MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from csf_tz.api.selcom import create_order_minimal
from csf_tz.api.utils import msgPrint, msgThrow
from csf_tz.csf_tz.dashboard_chart_source.multi_account_balance_timeline.multi_account_balance_timeline import (
	MultiBankBalance,
	create_sample_accounts,
	create_test_transactions,
	debug_chart_data,
	get,
	get_account_currencies,
	get_default_bank_account,
	get_sample_data,
	validate_chart_permissions,
)
from csf_tz.csftz_hooks.landed_cost_voucher import get_landed_cost_expenses, total_amount
from csf_tz.tests.import_fixtures import COMPANY, INR_BANK

NO_ROLE_USER = "csf-payments-noperm@example.com"


class TestSelcomOrders(IntegrationTestCase):
	def create_order(self, response=None, error=None):
		client = MagicMock()
		client.postFunc.return_value = response
		client.postFunc.side_effect = error
		with (
			patch("csf_tz.api.selcom.apigwClient.Client", return_value=client),
			patch("csf_tz.api.selcom.create_order_log") as create_order_log,
		):
			result = create_order_minimal()
		client.postFunc.assert_called_once_with("/checkout/create-order-minimal", ANY)
		return result, create_order_log

	def test_successful_order_is_logged(self):
		response = {"resultcode": "000", "reference": "REF-1"}
		result, create_order_log = self.create_order(response)
		self.assertEqual(result, response)
		self.assertEqual(create_order_log.call_args.args[1], "Success")
		self.assertEqual(create_order_log.call_args.kwargs["reference"], "REF-1")

	def test_rejected_order_is_logged_as_failed(self):
		result, create_order_log = self.create_order({"resultcode": "999", "reference": "REF-2"})
		self.assertEqual(result["resultcode"], "999")
		self.assertEqual(create_order_log.call_args.args[1], "Failed")

	def test_gateway_error_raises(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Failed to create order"):
			self.create_order(error=Exception("gateway down"))


class TestApiUtils(IntegrationTestCase):
	def test_msg_throw(self):
		with self.assertRaises(frappe.ValidationError):
			msgThrow("blocked")
		with patch("frappe.msgprint") as msgprint:
			msgThrow("warned", "validate")
		msgprint.assert_called_once_with("warned", alert=True)

	def test_msg_print(self):
		with patch("frappe.msgprint") as msgprint:
			msgPrint("validated", "validate")
			msgPrint("thrown")
		self.assertEqual(msgprint.call_args_list[0].kwargs, {"alert": True})
		self.assertEqual(msgprint.call_args_list[1].kwargs, {"alert": False})


class TestLandedCostVoucherHooks(IntegrationTestCase):
	def test_total_amount(self):
		voucher = frappe.new_doc("Landed Cost Voucher")
		voucher.append("items", {"amount": 100, "applicable_charges": 20})
		voucher.append("items", {"amount": 50, "applicable_charges": 0})
		total_amount(voucher, "validate")
		self.assertEqual([row.custom_total_amount for row in voucher.items], [120, 0])
		self.assertEqual(voucher.custom_grand_total, 120)
		voucher.items = []
		total_amount(voucher, "validate")
		self.assertEqual(voucher.custom_grand_total, 0)

	def test_landed_cost_expenses_without_import_file(self):
		self.assertIsNone(get_landed_cost_expenses())
		self.assertIsNone(get_landed_cost_expenses(""))


class TestMultiAccountBalanceTimeline(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", NO_ROLE_USER):
			frappe.get_doc(
				{"doctype": "User", "email": NO_ROLE_USER, "first_name": "No Perm", "send_welcome_email": 0}
			).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_get_returns_dataset_per_bank_account(self):
		data = get(filters=json.dumps({"company": COMPANY}))
		accounts = MultiBankBalance().get_bank_accounts(COMPANY, "Bank")
		self.assertEqual(data["account_count"], len(accounts))
		self.assertEqual(len(data["datasets"]), len(accounts))
		self.assertEqual(len(data["labels"]), 366)
		self.assertIn("_Test Bank", {dataset["name"] for dataset in data["datasets"]})
		self.assertEqual(data["summary"]["account_count"], len(accounts))

	def test_get_with_currency_filter(self):
		data = get(filters={"company": COMPANY, "currency": "USD"})
		self.assertEqual({dataset["name"] for dataset in data["datasets"]}, {"_Test Bank USD"})
		data = get(filters={"company": COMPANY, "currency": "JPY"})
		self.assertTrue(data["empty"])
		self.assertIn("No bank accounts found", data["message"])

	def test_get_without_company_returns_error_chart(self):
		for filters in ({}, "not json"):
			data = get(filters=filters)
			self.assertTrue(data["empty"])
			self.assertIn("Error retrieving", data["message"])

	def test_balances_follow_journal_entries(self):
		posting_date = add_days(nowdate(), -3)
		frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": COMPANY,
				"posting_date": posting_date,
				"accounts": [
					{"account": INR_BANK, "debit_in_account_currency": 1000},
					{"account": "Cash - _TC", "credit_in_account_currency": 1000},
				],
			}
		).submit()
		from_date, to_date = getdate(add_days(nowdate(), -10)), getdate(nowdate())
		balances = MultiBankBalance().get_account_balances([{"name": INR_BANK}], from_date, to_date)
		self.assertEqual(balances[to_date][INR_BANK] - balances[from_date][INR_BANK], 1000)
		self.assertEqual(
			balances[getdate(add_days(posting_date, -1))][INR_BANK], balances[from_date][INR_BANK]
		)
		data = get(filters={"company": COMPANY}, from_date=from_date, to_date=to_date)
		self.assertNotIn("empty", data)

	def test_filter_validation(self):
		validate = MultiBankBalance().validate_and_process_filters
		with self.assertRaisesRegex(frappe.ValidationError, "Company filter is required"):
			validate({})
		with self.assertRaisesRegex(frappe.ValidationError, "Invalid company"):
			validate({"company": "No Such Company"})
		self.assertEqual(validate({"company": COMPANY})["account_type"], "Bank")
		self.assertEqual(validate({"company": COMPANY, "account_type": "Weird"})["account_type"], "Bank")
		self.assertEqual(validate({"company": COMPANY, "account_type": "Cash"})["account_type"], "Cash")

	def test_date_range_validation(self):
		chart = MultiBankBalance()
		self.assertEqual(
			chart.validate_date_range(None, None), (getdate(add_days(nowdate(), -365)), getdate())
		)
		with self.assertRaisesRegex(frappe.ValidationError, "From Date cannot be after"):
			chart.validate_date_range("2026-02-01", "2026-01-01")
		with self.assertRaisesRegex(frappe.ValidationError, "cannot exceed"):
			chart.validate_date_range("2020-01-01", "2026-01-01")

	def test_date_range_intervals(self):
		chart = MultiBankBalance()
		self.assertEqual(len(chart.get_date_range(date(2026, 1, 1), date(2026, 1, 8))), 8)
		self.assertEqual(
			chart.get_date_range(date(2026, 1, 1), date(2026, 1, 20), "weekly"),
			[date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15), date(2026, 1, 20)],
		)
		self.assertEqual(
			chart.get_date_range(date(2026, 11, 15), date(2027, 2, 1), "monthly"),
			[date(2026, 11, 15), date(2026, 12, 15), date(2027, 1, 15), date(2027, 2, 1)],
		)

	def test_format_chart_data_and_summary(self):
		chart = MultiBankBalance()
		accounts = [{"name": "A", "account_name": "Acc A"}, {"name": "B", "account_name": ""}]
		balances = {date(2026, 1, 1): {"A": 100, "B": 50}, date(2026, 1, 2): {"A": 200, "B": 50}}
		data = chart.format_chart_data(balances, accounts, date(2026, 1, 1), date(2026, 1, 2))
		self.assertEqual(data["labels"], ["Jan 1", "Jan 2"])
		self.assertEqual(data["datasets"][0]["values"], [100, 200])
		self.assertEqual(data["datasets"][1]["name"], "B")
		self.assertEqual(data["summary"]["total_balance"], 250)
		self.assertEqual(data["summary"]["highest_balance_account"], "Acc A")
		self.assertEqual(data["summary"]["highest_balance"], 200)
		self.assertTrue(chart.format_chart_data({}, accounts, None, None)["empty"])
		self.assertEqual(chart.calculate_summary_stats({}, accounts), {})

	def test_sample_data(self):
		for data in (get_sample_data(), MultiBankBalance().get_sample_data()):
			self.assertTrue(data["is_sample_data"])
			self.assertEqual(len(data["datasets"]), 3)
			self.assertEqual(len(data["labels"]), 31)

	def test_debug_chart_data(self):
		result = debug_chart_data(COMPANY)
		self.assertEqual(result["step"], "completed_successfully")
		self.assertGreater(result["account_count"], 0)

	def test_colors_and_empty_chart(self):
		chart = MultiBankBalance()
		self.assertEqual(len(chart.get_chart_colors(12)), 12)
		self.assertEqual(chart.get_chart_colors(0), ["#1f77b4"])
		self.assertEqual(chart.empty_chart_data(None)["message"], "No data available")

	def test_create_sample_accounts(self):
		created = create_sample_accounts(COMPANY)
		self.assertEqual(len(created), 3)
		for account in created:
			self.assertEqual(frappe.db.get_value("Account", account, "parent_account"), "Bank Accounts - _TC")
		self.assertEqual(create_sample_accounts(COMPANY), [])
		with self.assertRaisesRegex(frappe.ValidationError, "does not exist"):
			create_sample_accounts("No Such Company")

	def test_create_test_transactions(self):
		result = create_test_transactions(COMPANY, INR_BANK, 500)
		self.assertTrue(result["success"])
		self.assertEqual([entry["amount"] for entry in result["created_entries"]], [500, 150])
		for entry in result["created_entries"]:
			self.assertEqual(frappe.db.get_value("Journal Entry", entry["journal_entry"], "docstatus"), 1)

	def test_permissions_for_restricted_user(self):
		frappe.set_user(NO_ROLE_USER)
		with self.assertRaisesRegex(frappe.ValidationError, "permission"):
			create_test_transactions(COMPANY)
		with self.assertRaisesRegex(frappe.ValidationError, "permission"):
			create_sample_accounts(COMPANY)
		self.assertEqual(MultiBankBalance().get_bank_accounts(COMPANY), [])
		self.assertIn("No bank accounts found", get(filters={"company": COMPANY})["message"])

	def test_helpers(self):
		self.assertEqual(
			get_default_bank_account(COMPANY), frappe.db.get_value("Company", COMPANY, "default_bank_account")
		)
		self.assertTrue({"INR", "USD"} <= set(get_account_currencies(COMPANY)))
		self.assertTrue(validate_chart_permissions("Any Chart"))
