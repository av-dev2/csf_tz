"""Drops the education DocTypes that moved to edu_tz from sites that do not run edu_tz."""

import frappe

MOVED_DOCTYPES = ("NMB Callback", "Student Applicant Fees")


def execute():
	if "edu_tz" in frappe.get_installed_apps():
		return

	for doctype in MOVED_DOCTYPES:
		if frappe.db.get_value("DocType", doctype, "module") != "CSF TZ":
			continue

		if frappe.db.count(doctype):
			frappe.logger().warning(f"{doctype} has records and moved to edu_tz; install edu_tz to keep it.")
			continue

		frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
