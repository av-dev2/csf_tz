from unittest.mock import patch

import frappe
from frappe.utils import date_diff, nowdate

from csf_tz.overrides.salary_slip import generate_password_for_pdf
from csf_tz.tests.hr_payroll_fixtures import (
	HRPayrollTestCase,
	assign_salary_structure,
	make_payroll_employee,
	make_salary_slip,
	set_csf_tz_settings,
	setup_payroll_master_data,
)

STRUCTURE = "_Test CSF Salary Slip Structure"


class TestSalarySlipOverride(HRPayrollTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_payroll_master_data()
		cls.employee = make_payroll_employee("csf_salary_slip@example.com", cell_number="0700000000")
		assign_salary_structure(cls.employee, STRUCTURE)

	def setUp(self):
		super().setUp()
		set_csf_tz_settings(
			enable_fixed_working_days_per_month=0,
			working_days_per_month=26,
			override_salary_slip_email_message=0,
			salary_slip_email_message="",
		)

	def test_fixed_working_days_cap_total_and_payment_days(self):
		set_csf_tz_settings(enable_fixed_working_days_per_month=1, working_days_per_month=10)
		slip = make_salary_slip(self.employee, STRUCTURE)
		self.assertEqual(slip.total_working_days, 10)
		self.assertEqual(slip.payment_days, 10)

	def test_fixed_working_days_do_not_raise_short_months(self):
		set_csf_tz_settings(enable_fixed_working_days_per_month=1, working_days_per_month=40)
		slip = make_salary_slip(self.employee, STRUCTURE)
		self.assertLess(slip.total_working_days, 40)
		self.assertEqual(slip.total_working_days, slip.payment_days)

	def test_working_days_untouched_when_disabled(self):
		slip = make_salary_slip(self.employee, STRUCTURE)
		month_days = date_diff(slip.end_date, slip.start_date) + 1
		self.assertGreater(slip.total_working_days, 10)
		self.assertLessEqual(slip.total_working_days, month_days)

	def test_email_uses_custom_message(self):
		set_csf_tz_settings(
			override_salary_slip_email_message=1, salary_slip_email_message="Dear staff, slip attached"
		)
		slip = make_salary_slip(self.employee, STRUCTURE)
		with (
			patch.object(frappe, "sendmail") as sendmail,
			patch.object(frappe, "attach_print", return_value={"fname": "x.pdf"}) as attach_print,
		):
			slip.email_salary_slip()

		email_args = sendmail.call_args.kwargs
		self.assertEqual(email_args["message"], "Dear staff, slip attached")
		self.assertEqual(email_args["recipients"], ["csf_salary_slip@example.com"])
		self.assertEqual(email_args["reference_name"], slip.name)
		self.assertIsNone(attach_print.call_args.kwargs["password"])

	def test_email_custom_message_with_password(self):
		set_csf_tz_settings(override_salary_slip_email_message=1, salary_slip_email_message="Hello")
		frappe.db.set_single_value("Payroll Settings", "encrypt_salary_slips_in_emails", 1)
		frappe.db.set_single_value("Payroll Settings", "password_policy", "{first_name}-{cell_number}")
		slip = make_salary_slip(self.employee, STRUCTURE)
		with (
			patch.object(frappe, "sendmail") as sendmail,
			patch.object(frappe, "attach_print", return_value={}) as attach_print,
		):
			slip.email_salary_slip()

		self.assertEqual(attach_print.call_args.kwargs["password"], "csf_salary_slip@example.com-0700000000")
		self.assertIn("password protected", sendmail.call_args.kwargs["message"])

	def test_email_custom_message_without_receiver(self):
		set_csf_tz_settings(override_salary_slip_email_message=1, salary_slip_email_message="Hello")
		frappe.db.set_value("Employee", self.employee, "prefered_email", "")
		slip = make_salary_slip(self.employee, STRUCTURE)
		with patch.object(frappe, "sendmail") as sendmail, patch.object(frappe, "msgprint") as msgprint:
			slip.email_salary_slip()
		sendmail.assert_not_called()
		self.assertIn("email not found", msgprint.call_args.args[0])

	def test_email_falls_back_to_hrms_message(self):
		slip = make_salary_slip(self.employee, STRUCTURE)
		with (
			patch.object(frappe, "sendmail") as sendmail,
			patch.object(frappe, "attach_print", return_value={}),
		):
			slip.email_salary_slip()
		self.assertEqual(sendmail.call_args.kwargs["message"], "Please see attachment")

	def test_generate_password_for_pdf(self):
		self.assertEqual(generate_password_for_pdf("{cell_number}", self.employee), "0700000000")
		self.assertEqual(generate_password_for_pdf("{name}", self.employee), self.employee)

	def test_slip_posting_date_is_today(self):
		slip = make_salary_slip(self.employee, STRUCTURE)
		self.assertEqual(str(slip.posting_date), nowdate())
