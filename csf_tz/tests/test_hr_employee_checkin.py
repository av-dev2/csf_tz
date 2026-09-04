from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.utils import add_days, get_time, getdate
from hrms.hr.doctype.shift_type.test_shift_type import make_shift_assignment, setup_shift_type

from csf_tz.csftz_hooks import employee_checkin as hooks
from csf_tz.tests.hr_payroll_fixtures import HRPayrollTestCase, make_payroll_employee, set_csf_tz_settings

SHIFT = "_Test CSF Checkin Shift"
DEFAULT_SHIFT = "_Test CSF Default Shift"


def make_checkin(employee, time, log_type="IN"):
	return frappe.get_doc(
		{"doctype": "Employee Checkin", "employee": employee, "time": time, "log_type": log_type}
	).insert()


class TestEmployeeCheckin(HRPayrollTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.date = getdate()
		cls.employee = make_payroll_employee("csf_checkin@example.com")
		cls.default_employee = make_payroll_employee("csf_checkin_default@example.com")
		cls.shift_type = setup_shift_type(shift_type=SHIFT)
		cls.default_shift = setup_shift_type(
			shift_type=DEFAULT_SHIFT, start_time="14:00:00", end_time="18:00:00"
		)
		make_shift_assignment(SHIFT, cls.employee, cls.date)
		frappe.db.set_value("Employee", cls.default_employee, "default_shift", DEFAULT_SHIFT)

	def setUp(self):
		super().setUp()
		set_csf_tz_settings(override_fetch_shift_details=1)

	def at(self, time, date=None):
		return datetime.combine(date or self.date, get_time(time))

	def test_checkin_inside_shift_sets_shift_details(self):
		log = make_checkin(self.employee, self.at("08:45:00"))
		self.assertEqual(log.shift, SHIFT)
		self.assertEqual(log.shift_start, self.at("08:00:00"))
		self.assertEqual(log.shift_end, self.at("12:00:00"))
		self.assertEqual(log.shift_actual_start, self.at("07:00:00"))
		self.assertEqual(log.shift_actual_end, self.at("13:00:00"))

	def test_checkin_outside_shift_clears_shift(self):
		log = make_checkin(self.employee, self.at("13:01:00"))
		self.assertIsNone(log.shift)

	def test_duplicate_log_rejected(self):
		make_checkin(self.employee, self.at("08:45:00"))
		self.assertRaises(frappe.ValidationError, make_checkin, self.employee, self.at("08:45:00"))

	def test_inactive_employee_rejected(self):
		frappe.db.set_value("Employee", self.employee, "status", "Inactive")
		self.assertRaises(frappe.ValidationError, make_checkin, self.employee, self.at("08:45:00"))

	def test_override_disabled_skips_csf_logic(self):
		set_csf_tz_settings(override_fetch_shift_details=0)
		with patch.object(hooks, "get_employee_shift_timings") as timings:
			log = make_checkin(self.employee, self.at("08:45:00"))
		timings.assert_not_called()
		self.assertEqual(log.shift, SHIFT)

	def test_get_shifts_for_date(self):
		shifts = hooks.get_shifts_for_date(self.employee, self.at("08:00:00"))
		self.assertEqual([shift.shift_type for shift in shifts], [SHIFT])
		self.assertEqual(
			hooks.get_shifts_for_date(self.employee, self.at("08:00:00", add_days(self.date, -1))), []
		)

	def test_get_employee_shift_uses_default_shift(self):
		timestamp = self.at("15:00:00")
		self.assertEqual(hooks.get_employee_shift(self.default_employee, timestamp), {})
		shift = hooks.get_employee_shift(self.default_employee, timestamp, consider_default_shift=True)
		self.assertEqual(shift.shift_type.name, DEFAULT_SHIFT)
		self.assertEqual(shift.start_datetime, self.at("14:00:00"))

	def test_get_employee_shift_timings(self):
		prev_shift, current, next_shift = hooks.get_employee_shift_timings(
			self.employee, self.at("08:45:00"), True
		)
		self.assertEqual(current.shift_type.name, SHIFT)
		self.assertEqual(next_shift.start_datetime, self.at("08:00:00") + timedelta(days=1))
		self.assertFalse(prev_shift)
