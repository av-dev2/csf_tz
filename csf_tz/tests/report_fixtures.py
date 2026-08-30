"""Shared helpers for the csf_tz report tests."""

import frappe
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.desk.query_report import run
from frappe.utils import add_days, get_first_day, get_last_day, today

COMPANY = "_Test Company"
ITEM = "_Test Item"
WAREHOUSE = "_Test Warehouse - _TC"
COST_CENTER = "_Test Cost Center - _TC"
BANK_ACCOUNT_GL = "_Test Bank - _TC"


def run_report(report_name, filters=None):
	"""Run a report the way the desk does and return columns and result rows."""
	result = run(report_name, filters=filters or {}, ignore_prepared_report=True)
	return result["columns"], result["result"]


def fieldnames(columns):
	names = []
	for column in columns:
		if isinstance(column, dict):
			names.append(column.get("fieldname") or column.get("label"))
		else:
			names.append(column.split(":")[0])
	return names


def as_dicts(columns, rows):
	names = fieldnames(columns)
	return [row if isinstance(row, dict) else dict(zip(names, row, strict=False)) for row in rows]


def date_range():
	return {"from_date": add_days(today(), -30), "to_date": add_days(today(), 30)}


def month_range():
	return {"from_date": get_first_day(today()), "to_date": get_last_day(today())}


def receive_stock(item_code=ITEM, qty=50, rate=100, warehouse=WAREHOUSE):
	return make_stock_entry(
		item_code=item_code,
		target=warehouse,
		qty=qty,
		rate=rate,
		company=COMPANY,
		posting_date=today(),
	)


def sell_stock(item_code=ITEM, qty=2, rate=500, **args):
	"""Submit a stock-updating Sales Invoice that skips the VFD fiscalisation checks."""
	invoice = create_sales_invoice(
		item_code=item_code, qty=qty, rate=rate, update_stock=1, posting_date=today(), do_not_save=1, **args
	)
	invoice.is_not_vfd_invoice = 1
	invoice.insert()
	invoice.submit()
	invoice.load_from_db()
	return invoice


def make_bank_account(account_name="_Test Report Bank Account", bank="_Test Report Bank"):
	if not frappe.db.exists("Bank", bank):
		frappe.get_doc({"doctype": "Bank", "bank_name": bank}).insert()
	name = f"{account_name} - {bank}"
	if not frappe.db.exists("Bank Account", name):
		frappe.get_doc(
			{
				"doctype": "Bank Account",
				"account_name": account_name,
				"bank": bank,
				"account": BANK_ACCOUNT_GL,
				"is_company_account": 1,
				"company": COMPANY,
			}
		).insert()
	return name


def make_bank_transaction(bank_account, deposit=0, withdrawal=0, description="Test transaction"):
	transaction = frappe.get_doc(
		{
			"doctype": "Bank Transaction",
			"date": today(),
			"bank_account": bank_account,
			"company": COMPANY,
			"deposit": deposit,
			"withdrawal": withdrawal,
			"description": description,
			"reference_number": frappe.generate_hash(length=8),
		}
	).insert()
	transaction.submit()
	return transaction


def make_currency_exchange(from_currency, to_currency, rate):
	for currency in (from_currency, to_currency):
		frappe.db.set_value("Currency", currency, "enabled", 1)
	return frappe.get_doc(
		{
			"doctype": "Currency Exchange",
			"date": today(),
			"from_currency": from_currency,
			"to_currency": to_currency,
			"exchange_rate": rate,
			"for_buying": 1,
			"for_selling": 1,
		}
	).insert()
