import frappe
from frappe.utils import add_days, add_months, get_first_day, getdate, nowdate
from hrms.payroll.doctype.salary_slip.test_salary_slip import set_salary_component_account

from csf_tz.csftz_hooks import additional_salary as hooks
from csf_tz.tests.hr_payroll_fixtures import (
	COMPANY,
	HRPayrollTestCase,
	assign_salary_structure,
	make_payroll_employee,
	set_csf_tz_settings,
	setup_payroll_master_data,
)

HOURLY_COMPONENT = "_Test CSF Hourly Component"
CASH_COMPONENT = "_Test CSF Cash Component"
CASH_COMPONENT_NO_ACCOUNT = "_Test CSF Cash Component No Account"


def make_component(name, **values):
	if frappe.db.exists("Salary Component", name):
		frappe.delete_doc("Salary Component", name, force=True)
	component = frappe.new_doc("Salary Component")
	component.salary_component = name
	component.salary_component_abbr = name.replace(" ", "")[:8]
	component.type = "Earning"
	component.update(values)
	component.insert()
	return component


class TestAdditionalSalary(HRPayrollTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_payroll_master_data()
		cls.employee = make_payroll_employee("csf_additional_salary@example.com")
		assign_salary_structure(
			cls.employee,
			"_Test CSF Additional Salary Structure",
			from_date=add_months(get_first_day(nowdate()), -3),
		)
		make_component(HOURLY_COMPONENT, based_on_hourly_rate=1, hourly_rate=150)
		set_salary_component_account(make_component(CASH_COMPONENT, create_cash_journal=1))
		make_component(CASH_COMPONENT_NO_ACCOUNT, create_cash_journal=1)

	def setUp(self):
		super().setUp()
		set_csf_tz_settings(
			working_hours_per_month=200,
			default_account_for_additional_component_cash_journal="Cash - _TC",
		)

	def make_additional_salary(self, component="HRA", amount=100, **values):
		doc = frappe.new_doc("Additional Salary")
		doc.employee = self.employee
		doc.company = COMPANY
		doc.salary_component = component
		doc.amount = amount
		doc.payroll_date = nowdate()
		doc.update(values)
		return doc.insert()

	def test_negative_amount_rejected_by_default(self):
		self.assertRaises(frappe.ValidationError, self.make_additional_salary, amount=-100)

	def test_negative_amount_allowed_for_flagged_earning(self):
		frappe.db.set_value("Salary Component", "HRA", "allow_negative", 1)
		doc = self.make_additional_salary(amount=-100)
		self.assertEqual(doc.amount, -100)

	def test_negative_amount_rejected_for_deduction_even_if_flagged(self):
		frappe.db.set_value("Salary Component", "Professional Tax", "allow_negative", 1)
		self.assertRaises(
			frappe.ValidationError, self.make_additional_salary, component="Professional Tax", amount=-5
		)

	def test_duplicate_overwrite_is_rejected(self):
		self.make_additional_salary(overwrite_salary_structure_amount=1).submit()
		self.assertRaises(
			frappe.ValidationError, self.make_additional_salary, overwrite_salary_structure_amount=1
		)

	def test_hourly_rate_sets_amount(self):
		doc = self.make_additional_salary(component=HOURLY_COMPONENT, amount=0, no_of_hours=10)
		self.assertEqual(doc.based_on_hourly_rate, 1)
		self.assertEqual(doc.hourly_rate, 150)
		self.assertEqual(doc.amount, 3750)

	def test_hourly_rate_requires_working_hours_setting(self):
		set_csf_tz_settings(working_hours_per_month=0)
		self.assertRaises(
			frappe.ValidationError, self.make_additional_salary, component=HOURLY_COMPONENT, no_of_hours=10
		)

	def test_get_employee_base_salary_in_hours(self):
		result = hooks.get_employee_base_salary_in_hours(self.employee, nowdate())
		self.assertEqual(result["base_salary_in_hours"], 250)

	def test_cash_journal_created_on_submit(self):
		doc = self.make_additional_salary(component=CASH_COMPONENT, amount=500)
		doc.submit()

		journal_entry = frappe.get_doc(
			"Journal Entry", {"referance_doctype": "Additional Salary", "referance_docname": doc.name}
		)
		self.assertEqual(journal_entry.voucher_type, "Cash Entry")
		self.assertEqual(journal_entry.total_debit, 500)
		self.assertEqual(journal_entry.docstatus, 0)
		accounts = {
			row.account: (row.debit_in_account_currency, row.credit_in_account_currency)
			for row in journal_entry.accounts
		}
		self.assertEqual(accounts["Cash - _TC"], (0, 500))
		self.assertEqual(accounts["Salary - _TC"], (500, 0))

	def test_cash_journal_requires_default_account(self):
		set_csf_tz_settings(default_account_for_additional_component_cash_journal=None)
		doc = self.make_additional_salary(component=CASH_COMPONENT, amount=500)
		self.assertRaises(frappe.ValidationError, doc.submit)

	def test_cash_journal_requires_component_account(self):
		doc = self.make_additional_salary(component=CASH_COMPONENT_NO_ACCOUNT, amount=500)
		self.assertRaises(frappe.ValidationError, doc.submit)

	def test_submit_updates_source_last_transaction_amount(self):
		source = self.make_additional_salary(amount=100, auto_repeat_frequency="Monthly")
		source.submit()
		child = self.make_additional_salary(amount=120, auto_created_based_on=source.name)
		child.submit()
		self.assertEqual(
			frappe.db.get_value("Additional Salary", source.name, "last_transaction_amount"), 120
		)

	def auto_created_from(self, source):
		return frappe.get_all(
			"Additional Salary",
			filters={"auto_created_based_on": source},
			fields=["name", "payroll_date", "amount", "auto_repeat_frequency", "docstatus"],
		)

	def test_generate_monthly_records(self):
		source = self.make_additional_salary(
			amount=300,
			payroll_date=add_months(nowdate(), -1),
			auto_repeat_frequency="Monthly",
			auto_repeat_end_date=add_months(nowdate(), 6),
		)
		source.submit()

		hooks.generate_additional_salary_records()

		created = self.auto_created_from(source.name)
		self.assertEqual(len(created), 1)
		self.assertEqual(created[0].payroll_date, getdate(nowdate()))
		self.assertEqual(created[0].amount, 300)
		self.assertEqual(created[0].auto_repeat_frequency, "None")
		self.assertEqual(created[0].docstatus, 0)
		self.assertEqual(
			frappe.db.get_value("Additional Salary", source.name, "last_transaction_date"), getdate(nowdate())
		)

		hooks.generate_additional_salary_records()
		self.assertEqual(len(self.auto_created_from(source.name)), 1)

	def test_generate_weekly_records(self):
		source = self.make_additional_salary(
			amount=50,
			payroll_date=add_days(nowdate(), -7),
			auto_repeat_frequency="Weekly",
			auto_repeat_end_date=add_months(nowdate(), 1),
		)
		source.submit()
		hooks.generate_additional_salary_records()
		created = self.auto_created_from(source.name)
		self.assertEqual(len(created), 1)
		self.assertEqual(created[0].payroll_date, getdate(nowdate()))

	def test_generate_skips_future_records(self):
		source = self.make_additional_salary(
			amount=50,
			payroll_date=nowdate(),
			auto_repeat_frequency="Monthly",
			auto_repeat_end_date=add_months(nowdate(), 6),
		)
		source.submit()
		hooks.generate_additional_salary_records()
		self.assertEqual(self.auto_created_from(source.name), [])

	def test_generate_rejects_unknown_frequency(self):
		source = self.make_additional_salary(
			amount=50,
			payroll_date=add_months(nowdate(), -1),
			auto_repeat_frequency="Monthly",
			auto_repeat_end_date=add_months(nowdate(), 6),
		)
		source.submit()
		frappe.db.set_value("Additional Salary", source.name, "auto_repeat_frequency", "Daily")
		self.assertRaises(frappe.ValidationError, hooks.generate_additional_salary_records)
