import frappe
from frappe.utils import get_year_ending, get_year_start, getdate
from hrms.hr.doctype.leave_encashment.test_leave_encashment import create_leave_encashment
from hrms.hr.doctype.leave_period.test_leave_period import create_leave_period
from hrms.hr.doctype.leave_policy.test_leave_policy import create_leave_policy
from hrms.hr.doctype.leave_policy_assignment.leave_policy_assignment import (
	create_assignment_for_multiple_employees,
)

from csf_tz.csftz_hooks import leave_encashment as hooks
from csf_tz.tests.hr_payroll_fixtures import (
	HRPayrollTestCase,
	assign_salary_structure,
	make_payroll_employee,
	setup_payroll_master_data,
)

LEAVE_TYPE = "_Test Leave Type Encashment"


class TestLeaveEncashment(HRPayrollTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_payroll_master_data()
		cls.employee = make_payroll_employee("csf_leave_encashment@example.com")
		year_start, year_end = getdate(get_year_start(getdate())), getdate(get_year_ending(getdate()))
		cls.leave_period = create_leave_period(year_start, year_end, "_Test Company")
		frappe.db.set_value(
			"Leave Type",
			LEAVE_TYPE,
			{
				"earning_component": "Leave Encashment",
				"deduction_component": "Professional Tax",
				"non_encashable_leaves": 5,
			},
		)
		leave_policy = create_leave_policy(leave_type=LEAVE_TYPE, annual_allocation=10)
		leave_policy.submit()
		create_assignment_for_multiple_employees(
			[cls.employee],
			frappe._dict(
				assignment_based_on="Leave Period",
				leave_policy=leave_policy.name,
				leave_period=cls.leave_period.name,
			),
		)
		assign_salary_structure(
			cls.employee, "_Test CSF Encashment Structure", leave_encashment_amount_per_day=50
		)

	def make_encashment(self, **values):
		args = {
			"employee": self.employee,
			"leave_type": LEAVE_TYPE,
			"leave_period": self.leave_period.name,
			"encashment_date": self.leave_period.to_date,
			"currency": "INR",
		}
		args.update(values)
		return create_leave_encashment(**args)

	def additional_salary(self, encashment):
		return frappe.get_doc("Additional Salary", {"ref_docname": encashment.name})

	def test_positive_days_select_earning(self):
		encashment = self.make_encashment()
		self.assertEqual(encashment.encashment_days, 5)
		self.assertEqual(encashment.encashment_amount, 250)
		self.assertEqual((encashment.is_earning, encashment.is_deduction), (1, 0))

		encashment.submit()
		additional_salary = self.additional_salary(encashment)
		self.assertEqual(additional_salary.type, "Earning")
		self.assertEqual(additional_salary.salary_component, "Leave Encashment")
		self.assertEqual(additional_salary.amount, 250)
		self.assertEqual(additional_salary.docstatus, 1)
		self.assertEqual(encashment.additional_salary, additional_salary.name)

	def test_negative_days_select_deduction(self):
		encashment = self.make_encashment(encashment_days=-2)
		self.assertEqual(encashment.encashment_amount, -100)
		self.assertEqual((encashment.is_earning, encashment.is_deduction), (0, 1))

		encashment.submit()
		additional_salary = self.additional_salary(encashment)
		self.assertEqual(additional_salary.type, "Deduction")
		self.assertEqual(additional_salary.salary_component, "Professional Tax")
		self.assertEqual(additional_salary.amount, 100)

		encashment.cancel()
		self.assertEqual(frappe.db.get_value("Additional Salary", additional_salary.name, "docstatus"), 2)

	def test_deduction_requires_deduction_component(self):
		frappe.db.set_value("Leave Type", LEAVE_TYPE, "deduction_component", None)
		encashment = self.make_encashment(encashment_days=-2)
		self.assertRaises(frappe.ValidationError, encashment.submit)

	def test_both_flags_rejected(self):
		encashment = self.make_encashment()
		encashment.is_earning = encashment.is_deduction = 1
		encashment.encashment_days = encashment.encashment_amount = 0
		self.assertRaises(frappe.ValidationError, hooks.validate_flags, encashment)

	def test_before_submit_rejects_invalid_amounts(self):
		encashment = self.make_encashment()
		encashment.encashment_amount = None
		self.assertRaises(frappe.ValidationError, encashment.before_submit)

		encashment.encashment_amount = 0
		encashment.is_earning = 1
		self.assertRaises(frappe.ValidationError, encashment.before_submit)

		encashment.encashment_days = -2
		encashment.encashment_amount = 100
		self.assertRaises(frappe.ValidationError, encashment.before_submit)
		self.assertEqual((encashment.is_earning, encashment.is_deduction), (0, 1))

	def test_before_submit_requires_selection(self):
		encashment = self.make_encashment()
		encashment.is_earning = encashment.is_deduction = 0
		encashment.encashment_days = encashment.encashment_amount = 0
		self.assertRaises(frappe.ValidationError, hooks.ensure_selection_before_submit, encashment)

	def test_get_salary_component_sources(self):
		encashment = self.make_encashment()
		self.assertEqual(hooks._get_salary_component(encashment, "deduction")[0], "Professional Tax")
		self.assertEqual(hooks._get_salary_component(encashment, "earning")[0], "Leave Encashment")
		frappe.db.set_value("Leave Type", LEAVE_TYPE, "earning_component", None)
		component, source = hooks._get_salary_component(encashment, "earning")
		self.assertIsNone(component)
		self.assertIn(LEAVE_TYPE, source)
