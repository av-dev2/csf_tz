// Copyright (c) 2016, Aakvatech and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Excise Duty Detailed Report"] = {
    "filters": [
        {
         "fieldname": "from_date",
         "fieldtype": "Date",
         "label": "From Date",
         "reqd": 1,
         "default": frappe.datetime.month_start()
        },
        {
         "fieldname": "to_date",
         "fieldtype": "Date",
         "label": "To Date",
         "reqd": 1,
         "default": frappe.datetime.get_today()
        }
    ]
}
