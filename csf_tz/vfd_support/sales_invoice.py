# Copyright (c) 2020, Aakvatech and contributors
# For license information, please see license.txt

import json
import re

import erpnext
import frappe
from erpnext.controllers.taxes_and_totals import get_itemised_tax
from frappe import _
from frappe.utils import flt


def vfd_validation(doc, method):
	if doc.is_return or doc.is_not_vfd_invoice:
		return
	if doc.base_net_total == 0:
		frappe.throw(_("Base net amount is zero. Correct the invoice and retry."))

	vfdplus_settings = None
	if frappe.db.exists("VFDPlus Settings", {"name": doc.company}):
		vfdplus_settings = frappe.get_doc("VFDPlus Settings", doc.company)

	tax_data = get_itemised_tax_breakup_html(doc)
	if not tax_data:
		frappe.throw(_("Taxes not set correctly"))

	for item in doc.items:
		if not item.item_code:
			frappe.throw(_(f"Item Code not set for item {item.item_name}"))
		if not item.item_tax_template:
			item_tax_template = frappe.get_value("Item", item.item_code, "default_tax_template")
			if not item_tax_template:
				frappe.throw(_(f"Item Taxes Template not set for item {item.item_code}"))
			else:
				item.item_tax_template = item_tax_template
		item_taxcode = get_item_taxcode(item.item_tax_template, item.item_code, doc.name)

		with_tax = 0
		other_tax = 0

		for _tax_name, tax_value in tax_data.get(item.item_code).items():
			if tax_value.get("tax_rate") == 18:
				with_tax += 1
			else:
				other_tax += tax_value.get("tax_amount")

		if other_tax:
			frappe.throw(_(f"Taxes not set correctly for Other Tax item {item.item_code}"))
		if item_taxcode == 1 and with_tax != 1:
			if vfdplus_settings and vfdplus_settings.vat_enabled:
				frappe.msgprint(_(f"Taxes is not set to 18pct for Standard Rate item {item.item_code}"))
			else:
				frappe.throw(_(f"Taxes not set correctly for Standard Rate item {item.item_code}"))
		elif item_taxcode != 1 and with_tax != 0:
			frappe.throw(_(f"Taxes not set correctly for Non Standard Rate item {item.item_code}"))

	if not doc.vfd_cust_id_type or not doc.vfd_cust_id:
		data = get_customer_id_info(doc.customer)
		if data.get("cust_id"):
			doc.vfd_cust_id = data.get("cust_id")
		if data.get("cust_id_type"):
			doc.vfd_cust_id_type = data.get("cust_id_type")


def get_customer_id_info(customer):
	data = {}
	cust_id, cust_id_type, mobile_no = frappe.get_value(
		"Customer", customer, ["vfd_cust_id", "vfd_cust_id_type", "mobile_no"]
	)
	if not cust_id:
		data["cust_id"] = ""
		data["cust_id_type"] = 6
	elif cust_id and not cust_id_type:
		frappe.throw(_("Please make sure to set VFD Customer ID Type in Customer Master"))
	else:
		data["cust_id"] = cust_id
		data["cust_id_type"] = int(cust_id_type[:1])

	data["mobile_no"] = remove_all_except_numbers(mobile_no) or ""
	return data


def get_item_taxcode(item_tax_template=None, item_code=None, invoice_name=None):
	if not item_tax_template:
		if item_code and invoice_name:
			frappe.throw(_(f"Item Taxes Template not set for item {item_code} in invoice {invoice_name}"))
		elif item_code:
			frappe.throw(_(f"Item Taxes Template not set for item {item_code}"))
		else:
			frappe.throw(_("Item Taxes Template not set"))

	taxcode = None
	if item_tax_template:
		vfd_taxcode = frappe.get_value("Item Tax Template", item_tax_template, "vfd_taxcode")
		if vfd_taxcode:
			taxcode = int(vfd_taxcode[:1])
		else:
			frappe.throw(_(f"VFD Tax Code not setup in {item_tax_template}"))
	return taxcode


def validate_cancel(doc, method):
	if doc.vfd_rctvnum:
		frappe.throw(
			_(
				"This invoice cannot be canceled as it is already sent to TRA. Please cancel it on TRA portal during VAT Filing."
			)
		)


def get_itemised_tax_breakup_html(doc):
	if not doc.taxes:
		return
	return get_itemised_tax_breakup_data(doc)


def get_item_inclusive_amount(item):
	if item.base_net_amount == item.base_amount:
		# this is basic rate included
		item_tax_rate = json.loads(item.item_tax_rate)
		if not item_tax_rate or item_tax_rate == {}:
			return item.base_amount
		else:
			for _key, value in item_tax_rate.items():
				if not value or value == 0.00:
					return flt(item.base_amount, 2)
				return flt(item.base_amount * (1 + (value / 100)), 2)  # 118% for 18% VAT
	else:
		return flt(item.base_amount, 2)


@erpnext.allow_regional
def get_itemised_tax_breakup_data(doc):
	return get_itemised_tax(doc)


def remove_special_characters(text):
	return re.sub("[^A-Za-z0-9 ]+", "", text)


def remove_all_except_numbers(text=None):
	if not text:
		return ""
	return re.sub("[^0-9]+", "", text)
