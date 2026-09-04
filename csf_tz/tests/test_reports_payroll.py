import frappe
from erpnext.projects.doctype.timesheet.test_timesheet import make_timesheet
from frappe.tests import IntegrationTestCase
from frappe.utils import add_months, get_first_day, get_last_day, today
from hrms.payroll.doctype.salary_slip.test_salary_slip import (
	make_deduction_salary_component,
	make_earning_salary_component,
)
from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip
from hrms.payroll.doctype.salary_structure.test_salary_structure import (
	create_salary_structure_assignment,
	make_salary_structure,
)

from csf_tz.tests.report_fixtures import COMPANY, as_dicts, run_report
from csf_tz.utils.create_custom_fields import create_fields_from_json, load_json

EMPLOYEE = "_T-Employee-00001"
STRUCTURE = "_Test Report Salary Structure"


def install_payroll_entry_cheque_fields():
	if not frappe.get_meta("Payroll Entry").has_field("cheque_number"):
		create_fields_from_json(load_json("16_payroll_entry_cheque.json"))
		frappe.clear_cache(doctype="Payroll Entry")


def make_submitted_salary_slip():
	"""Submit a previous-month slip on base 40000 and a current-month slip on base 50000."""
	make_earning_salary_component(setup=True, company_list=[COMPANY])
	make_deduction_salary_component(setup=True, company_list=[COMPANY])
	frappe.db.set_single_value("Payroll Settings", "email_salary_slip_to_employee", 0)
	frappe.db.set_value(
		"Employee", EMPLOYEE, {"salary_mode": "Bank", "bank_name": "Test Bank", "bank_ac_no": "12345"}
	)
	previous_month = add_months(today(), -1)
	make_salary_structure(
		STRUCTURE,
		"Monthly",
		employee=EMPLOYEE,
		from_date=get_first_day(previous_month),
		company=COMPANY,
		base=40000,
	)
	create_salary_structure_assignment(
		EMPLOYEE,
		STRUCTURE,
		from_date=get_first_day(today()),
		company=COMPANY,
		base=50000,
		allow_duplicate=True,
	)
	slips = []
	for posting_date in (previous_month, today()):
		slip = make_salary_slip(STRUCTURE, employee=EMPLOYEE, posting_date=posting_date)
		slip.insert()
		slip.submit()
		slips.append(slip)
	return slips[-1]


class TestPayrollReports(IntegrationTestCase):
	"""Runs the payroll and statutory reports of csf_tz against a submitted salary slip."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		install_payroll_entry_cheque_fields()
		cls.slip = make_submitted_salary_slip()
		cls.period = {"from_date": get_first_day(today()), "to_date": get_last_day(today())}

	def register_filters(self, **extra):
		return {
			"company": COMPANY,
			"currency": "INR",
			"docstatus": "Submitted",
			**self.period,
			**extra,
		}

	def test_salary_register_csf(self):
		columns, rows = run_report("Salary Register csf", self.register_filters())
		rows = as_dicts(columns, rows)
		employee_rows = [r for r in rows if r.get("employee") == EMPLOYEE]
		self.assertEqual(len(employee_rows), 1)
		self.assertEqual(employee_rows[0]["net_pay"], self.slip.net_pay)

	def test_salary_register_ctc(self):
		columns, rows = run_report("Salary Register CTC", self.register_filters())
		rows = as_dicts(columns, rows)
		self.assertTrue(any(r.get("employee") == EMPLOYEE for r in rows))

	def test_salary_register_summary(self):
		columns, rows = run_report("Salary Register Summary", self.register_filters())
		self.assertTrue(columns)
		self.assertTrue(rows)

	def test_salary_register_summary_with_components(self):
		columns, rows = run_report("Salary Register Summary with Components", self.register_filters())
		self.assertTrue(columns)
		self.assertTrue(rows)

	def test_salary_register_summary_with_monthly_comparison(self):
		columns, rows = run_report(
			"Salary Register Summary with Monthly Comparison", self.register_filters(based_on_department=1)
		)
		rows = as_dicts(columns, rows)
		self.assertTrue(any(r.get("total_cur_month") for r in rows))

	def test_employee_salary_register_with_monthly_comparison(self):
		columns, rows = run_report(
			"Employee Salary Register with Monthly Comparison", self.register_filters()
		)
		rows = as_dicts(columns, rows)
		self.assertTrue(any(r.get("employee") == EMPLOYEE for r in rows), rows)

	def test_bank_report(self):
		columns, rows = run_report("Bank Report", self.period)
		self.assertIn("Cheque No.", [c["label"] for c in columns])
		rows = as_dicts(columns, rows)
		employee_rows = [r for r in rows if r.get("employee_id") == EMPLOYEE]
		self.assertEqual(len(employee_rows), 1, rows)
		self.assertEqual(employee_rows[0]["net_pay"], self.slip.net_pay)

	def test_statutory_query_reports(self):
		for report_name in (
			"HESLB Return online",
			"ITX.215.03.E SDL Monthly Returns",
			"ITX.219.03.E Statement of Tax Withheld",
			"NSSF CON5 Monthly Contribution - Online Version",
			"Payroll for Mobile Payment",
			"WCF Employee",
		):
			columns, rows = run_report(report_name, self.period)
			self.assertTrue(columns, report_name)
			self.assertIsInstance(rows, list, report_name)

	def test_paye_report_mapping(self):
		columns, rows = run_report("PAYE Report Mapping", self.period)
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_loan_repayment_details_needs_lending_app(self):
		if frappe.db.exists("DocType", "Loan Repayment"):
			self.skipTest("Lending app is installed")
		with self.assertRaises(frappe.ValidationError):
			run_report("Loan Repayment Details", {"employee": EMPLOYEE})

	def test_loan_outstanding_needs_lending_app(self):
		if frappe.db.exists("DocType", "Loan"):
			self.skipTest("Lending app is installed")
		with self.assertRaises(frappe.db.ProgrammingError):
			run_report("Loan Outstanding", {})

	def test_monthly_timesheet_report(self):
		timesheet = make_timesheet(EMPLOYEE, company=COMPANY)
		columns, rows = run_report("Monthly Timesheet Report", self.period)
		rows = as_dicts(columns, rows)
		self.assertTrue(any(r.get("employee_name") == timesheet.employee_name for r in rows))
		self.assertTrue(any(r.get("hours_used") == 2 for r in rows))
		columns, rows = run_report("Monthly Timesheet Report", {**self.period, "hours_per_day": 1})
		self.assertTrue(rows)
