import base64

import frappe

from csf_tz.csftz_hooks.employee_contact_qr import generate_contact_qr
from csf_tz.tests.hr_payroll_fixtures import HRPayrollTestCase, make_payroll_employee


class TestEmployeeContactQR(HRPayrollTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.employee = make_payroll_employee("csf_contact_qr@example.com", cell_number="0711111111")

	def test_generates_png_qr_code(self):
		image = base64.b64decode(generate_contact_qr(self.employee))
		self.assertTrue(image.startswith(b"\x89PNG"))

	def test_requires_phone_or_email(self):
		frappe.db.set_value(
			"Employee", self.employee, {"cell_number": "", "company_email": "", "personal_email": ""}
		)
		self.assertRaises(frappe.ValidationError, generate_contact_qr, self.employee)

	def test_denies_unprivileged_user(self):
		frappe.set_user("test@example.com")
		try:
			self.assertRaises(frappe.PermissionError, generate_contact_qr, self.employee)
		finally:
			frappe.set_user("Administrator")
