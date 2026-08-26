"""Shared fixtures for the csf_tz HR and payroll tests."""

import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe.tests import IntegrationTestCase
from frappe.utils import get_first_day, nowdate
from hrms.payroll.doctype.payroll_entry.payroll_entry import get_start_end_dates
from hrms.payroll.doctype.payroll_entry.test_payroll_entry import get_payroll_entry
from hrms.payroll.doctype.salary_slip.test_salary_slip import (
	make_deduction_salary_component,
	make_earning_salary_component,
)
from hrms.payroll.doctype.salary_structure.salary_structure import (
	make_salary_slip as make_salary_slip_from_structure,
)
from hrms.payroll.doctype.salary_structure.test_salary_structure import make_salary_structure

COMPANY = "_Test Company"
PAYROLL_PAYABLE_ACCOUNT = "Payroll Payable - _TC"


class HRPayrollTestCase(IntegrationTestCase):
	"""Rolls each test back to a savepoint so class fixtures survive."""

	SAVEPOINT = "csf_tz_hr_test"

	def setUp(self):
		super().setUp()
		frappe.db.savepoint(self.SAVEPOINT)

	def tearDown(self):
		frappe.db.rollback(save_point=self.SAVEPOINT)
		frappe.clear_document_cache("CSF TZ Settings", "CSF TZ Settings")
		super().tearDown()


def set_csf_tz_settings(**values):
	for field, value in values.items():
		frappe.db.set_single_value("CSF TZ Settings", field, value)
	frappe.clear_document_cache("CSF TZ Settings", "CSF TZ Settings")


def setup_payroll_master_data():
	make_earning_salary_component(setup=True, company_list=[COMPANY])
	make_deduction_salary_component(setup=True, company_list=[COMPANY])
	frappe.db.set_value("Account", PAYROLL_PAYABLE_ACCOUNT, "account_type", "Payable")
	frappe.db.set_value("Company", COMPANY, "default_payroll_payable_account", PAYROLL_PAYABLE_ACCOUNT)
	frappe.db.set_single_value("Payroll Settings", "email_salary_slip_to_employee", 0)
	frappe.db.set_single_value("Payroll Settings", "payroll_based_on", "Leave")


def make_department(department_name):
	name = f"{department_name} - _TC"
	if not frappe.db.exists("Department", name):
		frappe.get_doc(
			{"doctype": "Department", "department_name": department_name, "company": COMPANY}
		).insert()
	return name


def make_payroll_employee(email, **kwargs):
	return make_employee(email, company=COMPANY, **kwargs)


def assign_salary_structure(employee, structure_name, base=50000, from_date=None, **other_details):
	return make_salary_structure(
		structure_name,
		"Monthly",
		employee,
		from_date=from_date or get_first_day(nowdate()),
		company=COMPANY,
		base=base,
		other_details=other_details or None,
	)


def make_test_payroll_entry(department=None, submit=True):
	dates = get_start_end_dates("Monthly", nowdate())
	payroll_entry = get_payroll_entry(
		start_date=dates.start_date,
		end_date=dates.end_date,
		payable_account=PAYROLL_PAYABLE_ACCOUNT,
		currency="INR",
		company=COMPANY,
		department=department,
		cost_center="Main - _TC",
	)
	if submit:
		payroll_entry.submit()
		payroll_entry.reload()
	return payroll_entry


def make_salary_slip(employee, salary_structure):
	slip = make_salary_slip_from_structure(salary_structure, employee=employee, posting_date=nowdate())
	slip.insert()
	return slip


def get_slips(payroll_entry, docstatus=None):
	filters = {"payroll_entry": payroll_entry}
	if docstatus is not None:
		filters["docstatus"] = docstatus
	return frappe.get_all("Salary Slip", filters=filters, pluck="name")
