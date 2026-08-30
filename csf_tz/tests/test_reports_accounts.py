from unittest.mock import patch

import frappe
from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_first_day, get_last_day, today

from csf_tz.tests.report_fixtures import (
	BANK_ACCOUNT_GL,
	COMPANY,
	COST_CENTER,
	as_dicts,
	date_range,
	fieldnames,
	make_bank_account,
	make_bank_transaction,
	make_currency_exchange,
	receive_stock,
	run_report,
	sell_stock,
)


class TestAccountsReports(IntegrationTestCase):
	"""Runs the accounting reports of csf_tz against freshly posted vouchers."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		receive_stock(qty=20, rate=100)
		cls.sales_invoice = sell_stock(qty=2, rate=500)
		cls.purchase_invoice = make_purchase_invoice(qty=5, rate=50, posting_date=today())
		cls.bank_account = make_bank_account()
		cls.journal_entry = make_journal_entry(
			BANK_ACCOUNT_GL,
			"_Test Account Cost for Goods Sold - _TC",
			1000,
			cost_center=COST_CENTER,
			submit=True,
		)
		cls.bank_transaction = make_bank_transaction(cls.bank_account, deposit=1000)
		cls.credit_note = sell_stock(qty=-1, rate=500, is_return=1, return_against=cls.sales_invoice.name)
		make_currency_exchange("USD", "TZS", 2500)

	def ageing_filters(self, **extra):
		filters = {
			"company": COMPANY,
			"ageing_based_on": "Posting Date",
			"report_date": today(),
			"range1": 30,
			"range2": 60,
			"range3": 90,
			"range4": 120,
		}
		filters.update(extra)
		return filters

	def test_accounts_receivable_multi_currency(self):
		columns, rows = run_report("Accounts Receivable Multi Currency", self.ageing_filters())
		rows = as_dicts(columns, rows)
		invoice_rows = [r for r in rows if r.get("voucher_no") == self.sales_invoice.name]
		self.assertEqual(len(invoice_rows), 1)
		# ERPNext books a return either against the invoice or against itself, so compare the net
		outstanding = frappe.db.get_value("Sales Invoice", self.sales_invoice.name, "outstanding_amount")
		returned = sum(
			frappe.get_all(
				"Sales Invoice",
				filters={"return_against": self.sales_invoice.name, "docstatus": 1},
				pluck="outstanding_amount",
			)
		)
		self.assertEqual(outstanding + returned, 500)
		self.assertEqual(invoice_rows[0]["outstanding"], 500)

	def test_accounts_receivable_summary_multi_currency(self):
		columns, rows = run_report(
			"Accounts Receivable Summary Multi Currency", self.ageing_filters(currency="INR")
		)
		rows = as_dicts(columns, rows)
		customer_rows = [r for r in rows if "_Test Customer" in r.values()]
		self.assertEqual(len(customer_rows), 1, rows)
		self.assertGreaterEqual(
			customer_rows[0]["total_outstanding_amt"], self.sales_invoice.outstanding_amount
		)

	def ledger_filters(self, **extra):
		filters = {
			"company": COMPANY,
			"from_date": add_days(today(), -1),
			"to_date": today(),
			"group_by": "Group by Voucher (Consolidated)",
			"include_default_book_entries": 1,
		}
		filters.update(extra)
		return filters

	def test_general_ledger_pro(self):
		columns, rows = run_report("General Ledger Pro", self.ledger_filters())
		rows = as_dicts(columns, rows)
		vouchers = {r.get("voucher_no") for r in rows}
		self.assertIn(self.sales_invoice.name, vouchers)
		self.assertIn(self.credit_note.name, vouchers)
		columns, rows = run_report("General Ledger Pro", self.ledger_filters(account=BANK_ACCOUNT_GL))
		self.assertIn(self.journal_entry.name, {r.get("voucher_no") for r in as_dicts(columns, rows)})

	def test_general_ledger_pro_presentation_currency(self):
		with patch("erpnext.accounts.report.utils.get_rate_as_at", return_value=2):
			columns, rows = run_report(
				"General Ledger Pro",
				self.ledger_filters(presentation_currency="USD", account="Debtors - _TC"),
			)
		rows = as_dicts(columns, rows)
		self.assertTrue(any(r.get("voucher_no") == self.sales_invoice.name for r in rows))

	def test_multi_currency_ledger(self):
		columns, rows = run_report("Multi-Currency Ledger", self.ledger_filters())
		rows = as_dicts(columns, rows)
		self.assertIn("foreign_currency", fieldnames(columns))
		self.assertIn(self.journal_entry.name, {r.get("voucher_no") for r in rows})

	def test_monthly_account_balance(self):
		columns, rows = run_report("Monthly Account Balance", {"account": [BANK_ACCOUNT_GL]})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_trial_balance_report_in_usd(self):
		columns, rows = run_report(
			"Trial Balance Report in USD",
			{
				"company": COMPANY,
				"fiscal_year": "_Test Fiscal Year 2026",
				"from_date": get_first_day(today()),
				"to_date": get_last_day(today()),
				"with_period_closing_entry": 1,
			},
		)
		rows = as_dicts(columns, rows)
		debtors = [r for r in rows if r.get("account") == "Debtors - _TC"]
		self.assertEqual(len(debtors), 1, rows)
		self.assertAlmostEqual(debtors[0]["debit"], self.sales_invoice.grand_total / 2500, places=2)

	def test_gross_profit_pro_by_invoice(self):
		columns, rows = run_report(
			"Gross Profit Pro",
			{"company": COMPANY, "group_by": "Invoice", **date_range()},
		)
		rows = as_dicts(columns, rows)
		invoice_rows = [r for r in rows if r.get("sales_invoice") == self.sales_invoice.name]
		self.assertEqual(len(invoice_rows), 1, rows)
		self.assertEqual(invoice_rows[0]["qty"], 1)
		self.assertEqual(invoice_rows[0]["selling_amount"], 500)
		self.assertEqual(invoice_rows[0]["buying_amount"], 100)
		self.assertEqual(invoice_rows[0]["gross_profit"], 400)

	def test_gross_profit_pro_by_item_group(self):
		columns, rows = run_report(
			"Gross Profit Pro",
			{"company": COMPANY, "group_by": "Item Group", **date_range()},
		)
		self.assertIn("item_group", fieldnames(columns))
		self.assertTrue(rows)

	def bank_filters(self):
		return {"bank_account": self.bank_account, **date_range()}

	def test_bank_ledger_summary(self):
		columns, rows = run_report("Bank Ledger Summary", self.bank_filters())
		rows = as_dicts(columns, rows)
		self.assertEqual(rows[0]["account"], BANK_ACCOUNT_GL)
		self.assertGreaterEqual(rows[0]["deposit"], 1000)

	def test_bank_transaction_summary(self):
		columns, rows = run_report("Bank Transaction Summary", self.bank_filters())
		rows = as_dicts(columns, rows)
		self.assertEqual(rows[0]["account"], BANK_ACCOUNT_GL)
		self.assertEqual(rows[0]["deposit"], 1000)

	def test_bank_trans_vs_gl_entry_report(self):
		columns, rows = run_report("Bank Trans vs GL Entry Report", self.bank_filters())
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_nmb_bank_charges_reports(self):
		for report_name in ("NMB Bank Charges in Bank Transaction", "NMB Bank Transaction not Bank Charges"):
			columns, rows = run_report(report_name, self.bank_filters())
			self.assertTrue(columns, report_name)
			self.assertIsInstance(rows, list, report_name)

	def test_credit_note_list(self):
		columns, rows = run_report("Credit Note List", date_range())
		self.assertTrue(any(self.credit_note.name in str(row) for row in rows))

	def test_monthly_sales_and_purchase_summary(self):
		year = today()[:4]
		columns, rows = run_report("Monthly Sales Summary", {"year": year})
		self.assertTrue(columns)
		self.assertTrue(rows)
		columns, rows = run_report("Monthly Purchase Summary", {"year": year})
		self.assertTrue(columns)
		self.assertTrue(rows)

	def test_gl_entry_summary_for_trading_account(self):
		make_purchase_invoice(
			qty=1, rate=70, posting_date=today(), expense_account="Cost of Goods Sold - _TC"
		)
		columns, rows = run_report("GL Entry Summary for Trading Account", date_range())
		rows = as_dicts(columns, rows)
		self.assertTrue(
			any(r["voucher_type"] == "Main Purchase Invoice" and r["value"] == 70 for r in rows), rows
		)

	def test_import_exchange_differences(self):
		columns, rows = run_report("Import Exchange Differences", {"company": COMPANY, **date_range()})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_report_permission(self):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "report-noroles@example.com",
				"first_name": "No Roles",
				"send_welcome_email": 0,
			}
		).insert()
		frappe.set_user(user.name)
		try:
			with self.assertRaises(frappe.PermissionError):
				run_report("General Ledger Pro", self.ledger_filters())
		finally:
			frappe.set_user("Administrator")

	def test_ledgers_with_user_permissions(self):
		frappe.set_user("test@example.com")
		try:
			for report_name in ("General Ledger Pro", "Multi-Currency Ledger"):
				columns, rows = run_report(report_name, self.ledger_filters())
				self.assertIn(self.journal_entry.name, {r.get("voucher_no") for r in as_dicts(columns, rows)})
		finally:
			frappe.set_user("Administrator")
