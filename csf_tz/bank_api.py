"""Deprecated NMB endpoints.

The NMB fee integration lives in edu_tz.edu_tz.nmb.api. These entry points stay
because NMB stores the callback URL of invoices submitted before the move.
"""

import frappe
from frappe import _


def get_callback_handler(name: str):
	if "edu_tz" not in frappe.get_installed_apps():
		frappe.throw(_("NMB fee callbacks moved to the edu_tz app. Install edu_tz to process them."))
	from edu_tz.edu_tz.nmb import api

	return getattr(api, name)


# nosemgrep: guest-whitelisted-method -- NMB posts payment callbacks unauthenticated
@frappe.whitelist(allow_guest=True)
def receive_callback(*args, **kwargs):
	return get_callback_handler("receive_callback")(*args, **kwargs)


# nosemgrep: guest-whitelisted-method -- NMB validates references unauthenticated
@frappe.whitelist(allow_guest=True)
def receive_validate_reference(*args, **kwargs):
	return get_callback_handler("receive_validate_reference")(*args, **kwargs)
