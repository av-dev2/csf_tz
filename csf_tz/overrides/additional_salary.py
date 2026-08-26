import frappe
from frappe import _
from hrms.hr.utils import validate_active_employee
from hrms.payroll.doctype.additional_salary.additional_salary import (
	AdditionalSalary as _AdditionalSalary,
)


class AdditionalSalary(_AdditionalSalary):
	def validate(self):
		validate_active_employee(self.employee)
		self.validate_dates()
		self.validate_salary_structure()
		self.validate_recurring_additional_salary_overlap()
		self.validate_employee_referral()
		self.validate_duplicate_additional_salary()
		self.validate_tax_component_overwrite()
		self.validate_accrual_component()

		if self.ref_doctype == "Employee Advance":
			self.validate_employee_advance_return()

		allow_negative = frappe.db.get_value(
			"Salary Component",
			{"name": self.salary_component, "type": "Earning"},
			"allow_negative",
		)
		if not allow_negative:
			if self.amount < 0:
				frappe.throw(_("Amount should not be less than zero"))
