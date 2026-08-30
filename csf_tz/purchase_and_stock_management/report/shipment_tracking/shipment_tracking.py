# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns()

	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date {}").format(filters.to_date))

	where_filter = {
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}
	where = ""
	if filters.order:
		where += "  AND tot.name = %(order)s "
		where_filter.update({"order": filters.order})

	if filters.supplier:
		where += "  AND tot.supplier = %(supplier)s "
		where_filter.update({"supplier": filters.supplier})

	data = frappe.db.sql(
		"""SELECT
				tot.name AS order_no,
				tot.supplier,
				tot.supplier_type,
				tot.shipped_date,
				tot.expected_arrival_date,
				tot.mode_of_transport,
				tot.bl_number,
				tot.arrival_date,
				tot.discharged_date,
				tot.clearing_company,
				tot.expected_clearing_completion_date,
				tot.clearing_completion_date
			FROM
				`tabOrder Track` tot
			WHERE
				tot.expected_arrival_date BETWEEN %(from_date)s AND %(to_date)s
		 """
		+ where,
		where_filter,
		as_dict=1,
	)
	return columns, data


def get_columns():
	return [
		{
			"fieldname": "order_no",
			"label": _("Order No"),
			"fieldtype": "Link",
			"options": "Order Track",
			"width": 150,
		},
		{
			"fieldname": "supplier",
			"label": _("Supplier"),
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 150,
		},
		{"fieldname": "supplier_type", "label": _("Supplier Type"), "fieldtype": "Data", "width": 150},
		{
			"fieldname": "mode_of_transport",
			"label": _("Mode of Transport"),
			"fieldtype": "Data",
			"width": 150,
		},
		{"fieldname": "shipped_date", "label": _("Shipping Date"), "fieldtype": "Date", "width": 150},
		{
			"fieldname": "expected_arrival_date",
			"label": _("Expected Arrival Date"),
			"fieldtype": "Date",
			"width": 150,
		},
		{"fieldname": "arrival_date", "label": _("Arrival Date"), "fieldtype": "Date", "width": 120},
		{"fieldname": "discharged_date", "label": _("Discharged Date"), "fieldtype": "Date", "width": 120},
		{"fieldname": "bl_number", "label": _("Bl No"), "fieldtype": "Data", "width": 150},
		{"fieldname": "clearing_company", "label": _("Clearing Company"), "fieldtype": "Data", "width": 150},
		{
			"fieldname": "expected_clearing_completion_date",
			"label": _("Expected Clearing Completion Date"),
			"fieldtype": "Date",
			"width": 150,
		},
		{
			"fieldname": "clearing_completion_date",
			"label": _("Clearing Completion Date"),
			"fieldtype": "Date",
			"width": 150,
		},
	]
