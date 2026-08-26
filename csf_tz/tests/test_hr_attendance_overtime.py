from datetime import timedelta

import frappe
from frappe.utils import (
	add_days,
	get_first_day,
	get_weekday,
	get_year_ending,
	get_year_start,
	getdate,
	nowdate,
)

from csf_tz.csftz_hooks import attendance as hooks
from csf_tz.tests.hr_payroll_fixtures import (
	COMPANY,
	HRPayrollTestCase,
	make_payroll_employee,
	set_csf_tz_settings,
)

SHIFT = "_Test CSF Overtime Shift"
HOLIDAY_LIST = "_Test CSF Overtime Holidays"


def seconds(value):
	if value in (None, ""):
		return None
	if isinstance(value, timedelta):
		return int(value.total_seconds())
	value = str(value).split(" ")[-1]
	hours, minutes, secs = (int(part) for part in value.split(":"))
	return hours * 3600 + minutes * 60 + secs


def make_holiday_list(holiday_date):
	frappe.delete_doc_if_exists("Holiday List", HOLIDAY_LIST, force=True)
	return frappe.get_doc(
		{
			"doctype": "Holiday List",
			"holiday_list_name": HOLIDAY_LIST,
			"from_date": get_year_start(nowdate()),
			"to_date": get_year_ending(nowdate()),
			"holidays": [{"holiday_date": holiday_date, "description": "Test holiday"}],
		}
	).insert()


def make_shift_type():
	frappe.delete_doc_if_exists("Shift Type", SHIFT, force=True)
	thresholds = {
		f"{day}_threshold": "08:00:00"
		for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
	}
	return frappe.get_doc(
		{
			"doctype": "Shift Type",
			"__newname": SHIFT,
			"start_time": "08:00:00",
			"end_time": "17:00:00",
			"enable_late_entry_marking": 1,
			"late_entry_grace_period": 15,
			"enable_early_exit_marking": 1,
			"early_exit_grace_period": 15,
			"overtime_holiday": HOLIDAY_LIST,
			**thresholds,
		}
	).insert()


class TestAttendanceOvertime(HRPayrollTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.employee = make_payroll_employee("csf_overtime@example.com", overtime_applicable=1)
		cls.holiday = cls.pick_day(offset=0)
		cls.workday = cls.pick_day(offset=1)
		make_holiday_list(cls.holiday)
		make_shift_type()

	@classmethod
	def pick_day(cls, offset):
		day = add_days(get_first_day(nowdate()), offset)
		while get_weekday(getdate(day)) == "Sunday":
			day = add_days(day, 1)
		return day

	def setUp(self):
		super().setUp()
		set_csf_tz_settings(enable_overtime_calculation=1)

	def make_attendance(self, in_time, out_time, date=None, status="Present", **values):
		date = date or self.workday
		doc = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee,
				"company": COMPANY,
				"attendance_date": date,
				"status": status,
				"shift": SHIFT,
				"in_time": f"{date} {in_time}",
				"out_time": f"{date} {out_time}",
				**values,
			}
		)
		return doc.insert()

	def test_disabled_setting_leaves_fields_empty(self):
		set_csf_tz_settings(enable_overtime_calculation=0)
		doc = self.make_attendance("07:30:00", "18:00:00")
		self.assertIsNone(doc.eligible_working_hours)
		self.assertIsNone(doc.excess_overtime_normal)

	def test_non_present_status_resets_fields(self):
		doc = self.make_attendance("07:30:00", "18:00:00", status="Absent")
		self.assertEqual(seconds(doc.eligible_working_hours), 0)
		self.assertEqual(seconds(doc.eligible_overtime_normal), 0)
		self.assertEqual(seconds(doc.excess_overtime_holiday), 0)

	def test_employee_without_overtime_flag_resets_fields(self):
		frappe.db.set_value("Employee", self.employee, "overtime_applicable", 0)
		doc = self.make_attendance("07:30:00", "18:00:00")
		self.assertEqual(doc.overtime_applicable, 0)
		self.assertEqual(seconds(doc.eligible_working_hours), 0)

	def test_normal_day_overtime_and_excess(self):
		doc = self.make_attendance("07:30:00", "18:00:00")
		self.assertEqual(seconds(doc.start_time), 8 * 3600)
		self.assertEqual(seconds(doc.eligible_working_hours), 9 * 3600)
		self.assertEqual(seconds(doc.eligible_overtime_normal), 1 * 3600)
		self.assertEqual(seconds(doc.excess_overtime_normal), 90 * 60)
		self.assertEqual(seconds(doc.eligible_overtime_holiday), 0)
		self.assertEqual(seconds(doc.excess_overtime_holiday), 0)
		self.assertEqual(
			frappe.db.get_value("Attendance", doc.name, "excess_overtime_normal"), timedelta(minutes=90)
		)

	def test_grace_periods_round_to_shift_times(self):
		doc = self.make_attendance("08:10:00", "16:50:00")
		self.assertEqual(seconds(doc.eligible_working_hours), 9 * 3600)
		self.assertEqual(seconds(doc.eligible_overtime_normal), 1 * 3600)
		self.assertEqual(seconds(doc.excess_overtime_normal), 0)

	def test_beyond_grace_uses_actual_times(self):
		doc = self.make_attendance("08:30:00", "16:00:00")
		self.assertEqual(seconds(doc.eligible_working_hours), 7 * 3600 + 30 * 60)
		self.assertEqual(seconds(doc.eligible_overtime_normal), 0)
		self.assertEqual(seconds(doc.excess_overtime_normal), 0)

	def test_holiday_counts_all_hours_as_holiday_overtime(self):
		doc = self.make_attendance("07:30:00", "18:00:00", date=self.holiday)
		self.assertEqual(seconds(doc.eligible_overtime_holiday), 9 * 3600)
		self.assertEqual(seconds(doc.excess_overtime_holiday), 90 * 60)
		self.assertEqual(seconds(doc.eligible_overtime_normal), 0)
		self.assertEqual(seconds(doc.excess_overtime_normal), 0)

	def test_on_approval_overtime_moves_hours_to_excess(self):
		frappe.db.set_value("Employee", self.employee, "on_approval_overtime", 1)
		doc = self.make_attendance("07:30:00", "18:00:00")
		self.assertEqual(doc.on_approval_overtime, 1)
		self.assertEqual(seconds(doc.eligible_overtime_normal), 0)
		self.assertEqual(seconds(doc.excess_overtime_normal), 2 * 3600 + 30 * 60)
		self.assertEqual(seconds(doc.eligible_overtime_holiday), 0)
		self.assertEqual(seconds(doc.excess_overtime_holiday), 0)
		self.assertEqual(
			frappe.db.get_value("Attendance", doc.name, "excess_overtime_normal"), timedelta(minutes=150)
		)

	def test_on_approval_overtime_on_holiday(self):
		frappe.db.set_value("Employee", self.employee, "on_approval_overtime", 1)
		doc = self.make_attendance("07:30:00", "18:00:00", date=self.holiday)
		self.assertEqual(seconds(doc.excess_overtime_holiday), 10 * 3600 + 30 * 60)
		self.assertEqual(seconds(doc.excess_overtime_normal), 0)

	def test_shift_without_overtime_holiday_throws(self):
		frappe.db.set_value("Shift Type", SHIFT, "overtime_holiday", None)
		self.assertRaises(frappe.ValidationError, self.make_attendance, "07:30:00", "18:00:00")

	def test_weekday_threshold_lookup(self):
		shift_type = frappe.get_doc("Shift Type", SHIFT)
		shift_type.friday_threshold = "06:00:00"
		friday = getdate(self.workday)
		while get_weekday(friday) != "Friday":
			friday = add_days(friday, 1)
		self.assertEqual(seconds(hooks.get_weekday_threshold(shift_type, friday)), 6 * 3600)
		self.assertEqual(seconds(hooks.get_weekday_threshold(shift_type, add_days(friday, 1))), 8 * 3600)

	def test_holiday_status(self):
		self.assertTrue(hooks.get_holiday_status(HOLIDAY_LIST, self.holiday))
		self.assertFalse(hooks.get_holiday_status(HOLIDAY_LIST, self.workday))
