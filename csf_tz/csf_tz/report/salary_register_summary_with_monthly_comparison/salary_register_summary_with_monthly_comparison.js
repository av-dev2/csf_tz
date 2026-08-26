// Copyright (c) 2022, Aakvatech and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Salary Register Summary with Monthly Comparison"] = {
	onload: function (report) {
		frappe.query_report.set_filter_value("based_on_department", 1);
		var department = frappe.query_report.get_filter("department");
		department.df.hidden = 0;
		department.refresh();
	},
	filters: [
		{
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
			width: "100px",
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
			width: "100px",
		},
		{
			fieldname: "currency",
			fieldtype: "Link",
			options: "Currency",
			label: __("Currency"),
			default: erpnext.get_currency(frappe.defaults.get_default("Company")),
			width: "50px",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			width: "100px",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			width: "100px",
			reqd: 1,
		},
		{
			fieldname: "docstatus",
			label: __("Document Status"),
			fieldtype: "Select",
			options: ["Draft", "Submitted", "Cancelled"],
			default: "Submitted",
			width: "100px",
		},
		{
			fieldname: "based_on_department",
			label: __("Based on Department"),
			fieldtype: "Check",
			// "default": 1,
			on_change: function () {
				var based_on_department = frappe.query_report.get_filter_value("based_on_department");
				var cost_center = frappe.query_report.get_filter("cost_center");
				var department = frappe.query_report.get_filter("department");
				if (based_on_department) {
					frappe.query_report.set_filter_value("based_on_cost_center", 0);
					frappe.query_report.set_filter_value("cost_center", "");
					cost_center.df.hidden = 1;
					department.df.hidden = 0;
				} else {
					frappe.query_report.set_filter_value("based_on_cost_center", 1);
					frappe.query_report.set_filter_value("department", "");
					department.df.hidden = 1;
					cost_center.df.hidden = 0;
				}
				cost_center.refresh();
				department.refresh();
			},
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
			default: "",
			width: "100px",
			hidden: 1,
			get_query: function () {
				var company = frappe.query_report.get_filter_value("company");
				return {
					doctype: "Department",
					filters: {
						company: company,
					},
				};
			},
		},
		{
			fieldname: "based_on_cost_center",
			label: __("Based on Cost Center"),
			fieldtype: "Check",
			default: 0,
			on_change: function () {
				var based_on_cost_center = frappe.query_report.get_filter_value("based_on_cost_center");
				var cost_center = frappe.query_report.get_filter("cost_center");
				var department = frappe.query_report.get_filter("department");
				if (based_on_cost_center) {
					frappe.query_report.set_filter_value("based_on_department", 0);
					frappe.query_report.set_filter_value("department", "");
					department.df.hidden = 1;
					cost_center.df.hidden = 0;
				} else {
					frappe.query_report.set_filter_value("based_on_department", 1);
					frappe.query_report.set_filter_value("cost_center", "");
					cost_center.df.hidden = 1;
					department.df.hidden = 0;
				}
				cost_center.refresh();
				department.refresh();
			},
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
			default: "",
			width: "100px",
			hidden: 1,
			get_query: function () {
				var company = frappe.query_report.get_filter_value("company");
				return {
					doctype: "Cost Center",
					filters: {
						company: company,
					},
				};
			},
		},
	],
};
