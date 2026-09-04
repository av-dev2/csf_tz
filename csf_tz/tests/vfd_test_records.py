"""Shared VFD test records: providers, settings, tax templates, customers and invoices."""

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import add_days, nowdate

COMPANY = "_Test Company"
CUSTOMER = "_Test VFD Customer"
ITEM = "_Test Non Stock Item"
SECOND_ITEM = "_Test VFD Service Item"
VAT_ACCOUNT = "_Test Account VAT - _TC"
STANDARD_TEMPLATE = "VFD Standard Rate - _TC"
EXEMPT_TEMPLATE = "VFD Exempt - _TC"
STANDARD_ZERO_TEMPLATE = "VFD Standard Zero - _TC"
STANDARD_OTHER_TEMPLATE = "VFD Standard Other - _TC"
NO_CODE_TEMPLATE = "VFD No Code - _TC"

PROVIDERS = {
	"VFDPlus": {
		"settings": "VFDPlus Settings",
		"base_url": "https://vfdplus.test/api/",
		"attributes": {"post_fiscal_receipt": "receipt", "serial_info": "serial", "account_info": "account"},
	},
	"TotalVFD": {
		"settings": "Total VFD Setting",
		"base_url": "https://totalvfd.test/",
		"attributes": {"sales": "sales"},
	},
	"SimplifyVFD": {
		"settings": "Simplify VFD Settings",
		"base_url": "https://simplify.test/",
		"attributes": {"login": "login", "refresh": "refresh", "createIssuedInvoice": "invoice"},
	},
}

SERIAL_INFO = {
	"msg_status": "OK",
	"msg_code": 2000,
	"msg_data": {"serial_id": "SER-1", "serial_code": "CRED-1", "tin": "123456789", "vat_enabled": 1},
}


def fake_response(body, status_code=200, ok=True):
	response = MagicMock()
	response.ok = ok
	response.status_code = status_code
	response.text = json.dumps(body)
	response.json.return_value = body
	response.url = "https://mocked.test/"
	response.headers = {}
	response.cookies = {}
	return response


def make_item_tax_template(name, vfd_taxcode, rate):
	if frappe.db.exists("Item Tax Template", name):
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Item Tax Template",
			"title": name.replace(" - _TC", ""),
			"company": COMPANY,
			"vfd_taxcode": vfd_taxcode,
			"taxes": [{"tax_type": VAT_ACCOUNT, "tax_rate": rate}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def make_item_tax_templates():
	make_item_tax_template(STANDARD_TEMPLATE, "1- Standard Rate (18%)", 18)
	make_item_tax_template(EXEMPT_TEMPLATE, "5- Exempt", 0)
	make_item_tax_template(STANDARD_ZERO_TEMPLATE, "1- Standard Rate (18%)", 0)
	make_item_tax_template(STANDARD_OTHER_TEMPLATE, "1- Standard Rate (18%)", 10)
	make_item_tax_template(NO_CODE_TEMPLATE, "", 18)


def make_second_item():
	if frappe.db.exists("Item", SECOND_ITEM):
		return SECOND_ITEM
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": SECOND_ITEM,
			"item_name": SECOND_ITEM,
			"item_group": "_Test Item Group",
			"stock_uom": "Nos",
			"is_stock_item": 0,
		}
	).insert(ignore_permissions=True)
	return SECOND_ITEM


def make_customer(name=CUSTOMER, tax_id="123-456-789"):
	if frappe.db.exists("Customer", name):
		return frappe.get_doc("Customer", name)
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": name,
			"customer_group": "_Test Customer Group",
			"territory": "_Test Territory",
			"tax_id": tax_id,
			"mobile_no": "+255 (0)712-345-678",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def make_vfd_providers():
	for name, info in PROVIDERS.items():
		if frappe.db.exists("VFD Provider", name):
			continue
		frappe.get_doc(
			{
				"doctype": "VFD Provider",
				"vfd_provider": name,
				"vfd_provider_settings": info["settings"],
				"base_url": info["base_url"],
				"attributes": [{"key": key, "value": value} for key, value in info["attributes"].items()],
			}
		).insert(ignore_permissions=True)


def set_company_provider(provider, company=COMPANY):
	if frappe.db.exists("Company VFD Provider", company):
		doc = frappe.get_doc("Company VFD Provider", company)
		doc.vfd_provider = provider
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{"doctype": "Company VFD Provider", "company": company, "vfd_provider": provider}
		).insert(ignore_permissions=True)
	frappe.clear_document_cache("Company VFD Provider", company)
	return doc


def make_vfdplus_settings(company=COMPANY):
	if frappe.db.exists("VFDPlus Settings", company):
		return frappe.get_doc("VFDPlus Settings", company)
	doc = frappe.get_doc(
		{
			"doctype": "VFDPlus Settings",
			"company": company,
			"vfdplus_api_key": "plus-key",
			"vfd_start_date": add_days(nowdate(), -30),
		}
	)
	with patch(
		"csf_tz.vfd_providers.doctype.vfdplus_settings.vfdplus_settings.requests.request",
		return_value=fake_response(SERIAL_INFO),
	):
		doc.insert(ignore_permissions=True)
	return doc


def make_total_vfd_setting(company=COMPANY):
	if frappe.db.exists("Total VFD Setting", company):
		return frappe.get_doc("Total VFD Setting", company)
	return frappe.get_doc(
		{
			"doctype": "Total VFD Setting",
			"company": company,
			"serial_id": "TOTAL-SERIAL",
			"bearer_token": "total-token",
			"x_active_business": "business-1",
			"vfd_start_date": add_days(nowdate(), -30),
		}
	).insert(ignore_permissions=True)


def make_simplify_settings(company=COMPANY):
	if frappe.db.exists("Simplify VFD Settings", company):
		return frappe.get_doc("Simplify VFD Settings", company)
	return frappe.get_doc(
		{
			"doctype": "Simplify VFD Settings",
			"company": company,
			"username": "simplify-user",
			"password": "simplify-pass",
			"bearer_token": "simplify-token",
			"refresh_token": "simplify-refresh",
			"vfd_start_date": add_days(nowdate(), -30),
		}
	).insert(ignore_permissions=True)


def make_sales_invoice(
	item_tax_template=STANDARD_TEMPLATE, tax_rate=18, with_taxes=True, submit=False, **values
):
	items = values.pop("items", None) or [{"item_code": ITEM, "qty": 2, "rate": 100}]
	doc = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": COMPANY,
			"customer": values.pop("customer", CUSTOMER),
			"debit_to": "Debtors - _TC",
			"currency": "INR",
			"conversion_rate": 1,
			"set_posting_time": 1,
			"posting_date": nowdate(),
			"due_date": nowdate(),
			**values,
		}
	)
	for item in items:
		doc.append(
			"items",
			{
				"item_tax_template": item_tax_template,
				"income_account": "Sales - _TC",
				"cost_center": "_Test Cost Center - _TC",
				**item,
			},
		)
	if with_taxes:
		doc.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": VAT_ACCOUNT,
				"rate": tax_rate,
				"description": "VAT",
				"cost_center": "_Test Cost Center - _TC",
			},
		)
	doc.insert()
	if submit:
		doc.submit()
	return doc


def make_vfd_records():
	"""Create every VFD test record on the test company."""
	make_item_tax_templates()
	make_second_item()
	make_customer()
	make_vfd_providers()
	make_total_vfd_setting()
	make_simplify_settings()
	make_vfdplus_settings()
