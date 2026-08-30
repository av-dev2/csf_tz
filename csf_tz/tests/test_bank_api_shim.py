from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from csf_tz import bank_api


class TestBankApiShim(IntegrationTestCase):
	def test_throws_when_edu_tz_is_not_installed(self):
		with patch("frappe.get_installed_apps", return_value=["frappe", "erpnext", "csf_tz"]):
			with self.assertRaises(frappe.ValidationError):
				bank_api.get_callback_handler("receive_callback")

	def test_forwards_to_edu_tz_when_installed(self):
		if "edu_tz" not in frappe.get_installed_apps():
			self.skipTest("edu_tz is not installed on this site")
		from edu_tz.edu_tz.nmb import api

		self.assertIs(bank_api.get_callback_handler("receive_callback"), api.receive_callback)
		self.assertIs(
			bank_api.get_callback_handler("receive_validate_reference"), api.receive_validate_reference
		)
