"""Shared factories for the custom_api integration tests."""

from unittest.mock import patch

import frappe
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.utils import nowdate

COMPANY = "_Test Company"
CUSTOMER = "_Test Customer"
SUPPLIER = "_Test Supplier"
WAREHOUSE = "_Test Warehouse - _TC"
COST_CENTER = "_Test Cost Center - _TC"


def disable_db_commit(test_case):
	"""App code commits inside hooks; keep the class rollback effective."""
	patcher = patch.object(frappe.db, "commit")
	patcher.start()
	test_case.addCleanup(patcher.stop)


def disable_db_commit_for_class(test_class):
	patcher = patch.object(frappe.db, "commit")
	patcher.start()
	test_class.addClassCleanup(patcher.stop)


def set_csf_settings(**values):
	for fieldname, value in values.items():
		frappe.db.set_single_value("CSF TZ Settings", fieldname, value)


def make_test_item(item_code, **properties):
	properties.setdefault("is_stock_item", 1)
	return make_item(item_code, properties)


def add_stock(item_code, qty, rate=100, warehouse=WAREHOUSE, **args):
	return make_stock_entry(
		item_code=item_code, qty=qty, rate=rate, to_warehouse=warehouse, company=COMPANY, **args
	)


def make_sales_invoice(**args):
	args = frappe._dict(args)
	invoice = frappe.new_doc("Sales Invoice")
	invoice.update(
		{
			"company": COMPANY,
			"customer": args.customer or CUSTOMER,
			"debit_to": "Debtors - _TC",
			"posting_date": args.posting_date or nowdate(),
			"set_posting_time": 1,
			"currency": "INR",
			"conversion_rate": 1,
			"update_stock": args.update_stock or 0,
			"is_pos": args.is_pos or 0,
			"is_return": args.is_return or 0,
			"return_against": args.return_against,
			"set_warehouse": args.set_warehouse,
			"is_not_vfd_invoice": 1,
			"ignore_pricing_rule": 1,
		}
	)
	for row in args.rows or [{}]:
		invoice.append("items", sales_invoice_row(args, row))
	if args.do_not_save:
		return invoice
	invoice.insert()
	if not args.do_not_submit:
		invoice.submit()
	return invoice


def sales_invoice_row(args, row):
	row = frappe._dict(row)
	return {
		"item_code": row.item_code or args.item_code or "_Test Item",
		"qty": row.qty if row.qty is not None else (args.qty if args.qty is not None else 1),
		"rate": row.rate if row.rate is not None else (args.rate if args.rate is not None else 100),
		"price_list_rate": row.price_list_rate or args.price_list_rate or 0,
		"discount_amount": row.discount_amount or args.discount_amount or 0,
		"warehouse": row.warehouse or args.warehouse or WAREHOUSE,
		"conversion_factor": row.conversion_factor or args.conversion_factor or 1,
		"stock_qty": row.stock_qty or 0,
		"income_account": "Sales - _TC",
		"expense_account": "Cost of Goods Sold - _TC",
		"cost_center": COST_CENTER,
		"allow_override_net_rate": row.allow_override_net_rate or args.allow_override_net_rate or 0,
	}


def make_purchase_invoice(**args):
	args = frappe._dict(args)
	invoice = frappe.new_doc("Purchase Invoice")
	invoice.update(
		{
			"company": COMPANY,
			"supplier": SUPPLIER,
			"credit_to": "Creditors - _TC",
			"posting_date": nowdate(),
			"set_posting_time": 1,
			"currency": "INR",
			"conversion_rate": 1,
			"ignore_pricing_rule": 1,
		}
	)
	invoice.append(
		"items",
		{
			"item_code": args.item_code or "_Test Item",
			"qty": args.qty if args.qty is not None else 1,
			"rate": args.rate if args.rate is not None else 50,
			"warehouse": args.warehouse or WAREHOUSE,
			"expense_account": "_Test Account Cost for Goods Sold - _TC",
			"cost_center": COST_CENTER,
		},
	)
	if args.do_not_save:
		return invoice
	invoice.insert()
	if not args.do_not_submit:
		invoice.submit()
	return invoice


def make_delivery_note(**args):
	args = frappe._dict(args)
	note = frappe.new_doc("Delivery Note")
	note.update(
		{
			"company": COMPANY,
			"customer": args.customer or CUSTOMER,
			"posting_date": args.posting_date or nowdate(),
			"set_posting_time": 1,
			"currency": "INR",
			"conversion_rate": 1,
			"ignore_pricing_rule": 1,
		}
	)
	note.append(
		"items",
		{
			"item_code": args.item_code or "_Test Item",
			"qty": args.qty if args.qty is not None else 1,
			"rate": args.rate if args.rate is not None else 100,
			"warehouse": args.warehouse or WAREHOUSE,
			"expense_account": "Cost of Goods Sold - _TC",
			"cost_center": COST_CENTER,
		},
	)
	note.insert()
	if not args.do_not_submit:
		note.submit()
	return note
