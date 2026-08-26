from unittest.mock import patch

import frappe
from hrms.hr.doctype.employee_advance.test_employee_advance import make_employee_advance

from csf_tz.csftz_hooks import employee_advance_payment_and_expense as hooks
from csf_tz.tests.hr_payroll_fixtures import COMPANY, HRPayrollTestCase, make_payroll_employee


def disable_payment_reference_fetch_fields():
	"""csf_tz fetch_from fields on Payment Entry Reference read from_date/to_date, which Employee Advance lacks."""
	for fieldname in ("start_date", "end_date"):
		frappe.db.set_value("Custom Field", f"Payment Entry Reference-{fieldname}", "fetch_from", None)
	frappe.clear_cache(doctype="Payment Entry Reference")


def make_travel_request(employee):
	if not frappe.db.exists("Purpose of Travel", "_Test CSF Purpose"):
		frappe.get_doc({"doctype": "Purpose of Travel", "purpose_of_travel": "_Test CSF Purpose"}).insert()
	return frappe.get_doc(
		{
			"doctype": "Travel Request",
			"employee": employee,
			"travel_type": "Domestic",
			"purpose_of_travel": "_Test CSF Purpose",
			"company": COMPANY,
		}
	).insert()


class TestEmployeeAdvancePayment(HRPayrollTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.employee = make_payroll_employee("csf_employee_advance@example.com")
		frappe.db.set_value("Account", "_Test Employee Advance - _TC", "account_type", "Receivable")
		cls.travel_request = make_travel_request(cls.employee)
		disable_payment_reference_fetch_fields()

	def payment_entries(self, advance):
		return frappe.get_all(
			"Payment Entry",
			filters={"reference_no": advance.name, "docstatus": ["!=", 2]},
			fields=["name", "paid_amount", "party", "party_type", "docstatus", "payment_type"],
		)

	def test_submit_with_travel_request_creates_payment_entry(self):
		advance = make_employee_advance(self.employee, {"travel_request_ref": self.travel_request.name})
		entries = self.payment_entries(advance)
		self.assertEqual(len(entries), 1)
		self.assertEqual(entries[0].paid_amount, 1000)
		self.assertEqual(entries[0].party, self.employee)
		self.assertEqual(entries[0].party_type, "Employee")
		self.assertEqual(entries[0].payment_type, "Pay")
		self.assertEqual(entries[0].docstatus, 0)

	def test_submit_without_travel_request_does_nothing(self):
		advance = make_employee_advance(self.employee)
		self.assertEqual(self.payment_entries(advance), [])

	def test_existing_payment_entry_is_not_duplicated(self):
		advance = make_employee_advance(self.employee, {"travel_request_ref": self.travel_request.name})
		with patch.object(frappe, "msgprint") as msgprint:
			hooks.execute(advance, "on_submit")
		self.assertIn("already exists", msgprint.call_args.args[0])
		self.assertEqual(len(self.payment_entries(advance)), 1)

	def test_payment_entry_failure_is_raised(self):
		with patch.object(hooks, "create_payment_entry", side_effect=Exception("boom")):
			self.assertRaisesRegex(
				frappe.ValidationError,
				"boom",
				make_employee_advance,
				self.employee,
				{"travel_request_ref": self.travel_request.name},
			)
