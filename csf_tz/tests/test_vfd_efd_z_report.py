import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from csf_tz.tests.vfd_test_records import (
	EXEMPT_TEMPLATE,
	make_customer,
	make_item_tax_templates,
	make_sales_invoice,
)

FUTURE = f"{add_days(nowdate(), 1)} 00:00:00"


def make_device(location="HQ", make="Datecs", model="DP25"):
	name = f"{location}-{make}-{model}"
	if frappe.db.exists("Electronic Fiscal Device", name):
		return frappe.get_doc("Electronic Fiscal Device", name)
	return frappe.get_doc(
		{
			"doctype": "Electronic Fiscal Device",
			"type": "Electronic Tax Register (ETR)",
			"serial_no": f"SN-{name}",
			"location": location,
			"make": make,
			"model": model,
		}
	).insert()


def make_report(device, z_no="Z1", z_report_date_time=FUTURE, **values):
	return frappe.get_doc(
		{
			"doctype": "EFD Z Report",
			"electronic_fiscal_device": device.name,
			"z_no": z_no,
			"receipts_issued": 1,
			"z_report_date_time": z_report_date_time,
			**values,
		}
	)


def row_for(report, invoice):
	return next(row for row in report.efd_z_report_invoices if row.invoice_number == invoice.name)


class TestElectronicFiscalDevice(IntegrationTestCase):
	def test_name_is_built_from_location_make_and_model(self):
		device = make_device("Branch", "Incotex", "M1")
		self.assertEqual(device.name, "Branch-Incotex-M1")
		self.assertEqual(device.serial_no, "SN-Branch-Incotex-M1")


class TestEFDZReport(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_item_tax_templates()
		make_customer()
		cls.device = make_device()
		cls.other_device = make_device("HQ", "Other", "X1")
		cls.standard_invoice = make_sales_invoice(electronic_fiscal_device=cls.device.name, submit=True)
		cls.exempt_invoice = make_sales_invoice(item_tax_template=EXEMPT_TEMPLATE, submit=True)
		cls.other_invoice = make_sales_invoice(electronic_fiscal_device=cls.other_device.name, submit=True)

	def ticked_report(self, **values):
		report = make_report(self.device, **values)
		report.get_sales_invoice()
		row_for(report, self.standard_invoice).include = 1
		report.update(
			{
				"total_turnover": 236,
				"net_amount": 200,
				"total_vat": 36,
				"total_turnover_ex_sr": 0,
				"total_turnover_ticked": 236,
				"total_excluding_vat_ticked": 200,
				"total_vat_ticked": 36,
				"total_turnover_exempted__sp_relief_ticked": 0,
			}
		)
		return report

	def test_validate_requires_invoices(self):
		self.assertRaisesRegex(
			frappe.ValidationError, "No Sales Invoie Found", make_report(self.device).insert
		)

	def test_get_sales_invoice_fetches_unlinked_invoices_for_device(self):
		report = make_report(self.device)
		self.assertTrue(report.get_sales_invoice())
		fetched = {row.invoice_number for row in report.efd_z_report_invoices}
		self.assertIn(self.standard_invoice.name, fetched)
		self.assertIn(self.exempt_invoice.name, fetched)
		self.assertNotIn(self.other_invoice.name, fetched)

		standard = row_for(report, self.standard_invoice)
		self.assertEqual(standard.amt_excl_vat, 200)
		self.assertEqual(standard.vat, 36)
		self.assertEqual(standard.amt_ex__sr, 0)
		self.assertEqual(standard.invoice_amount, 236)
		self.assertEqual(standard.invoice_currency, "INR")
		self.assertEqual(str(standard.invoice_date), nowdate())

		exempt = row_for(report, self.exempt_invoice)
		self.assertEqual(exempt.vat, 0)
		self.assertEqual(exempt.amt_ex__sr, 200)

	def test_get_sales_invoice_respects_report_time(self):
		report = make_report(self.device, z_report_date_time="2000-01-01 00:00:00")
		self.assertRaisesRegex(frappe.ValidationError, "No Sales Invoice Fetch", report.get_sales_invoice)

	def test_get_number_of_ticked(self):
		report = self.ticked_report()
		self.assertEqual(report.get_number_of_ticked(), 1)
		row_for(report, self.exempt_invoice).include = 1
		self.assertEqual(report.get_number_of_ticked(), 2)

	def test_submit_links_included_invoices_and_cancel_unlinks(self):
		report = self.ticked_report()
		report.insert()
		self.assertEqual(report.name, f"{self.device.name}-Z1")
		report.submit()
		self.assertEqual(len(report.efd_z_report_invoices), 1)
		self.assertEqual(
			frappe.db.get_value("Sales Invoice", self.standard_invoice.name, "efd_z_report"), report.name
		)
		self.assertFalse(frappe.db.get_value("Sales Invoice", self.exempt_invoice.name, "efd_z_report"))

		second = make_report(self.device, z_no="Z2")
		second.get_sales_invoice()
		fetched = {row.invoice_number for row in second.efd_z_report_invoices}
		self.assertNotIn(self.standard_invoice.name, fetched)

		report.cancel()
		self.assertFalse(frappe.db.get_value("Sales Invoice", self.standard_invoice.name, "efd_z_report"))

	def test_submit_throws_when_totals_do_not_match(self):
		checks = {
			"total_turnover": "not equal to Money Entered",
			"net_amount": "Total Excluding VAT",
			"total_vat": "Total VAT",
			"total_turnover_ex_sr": "Sp. Relief",
		}
		for fieldname, message in checks.items():
			report = self.ticked_report(z_no=f"Z-{fieldname}")
			report.set(fieldname, report.get(fieldname) + 5)
			report.insert()
			self.assertRaisesRegex(frappe.ValidationError, message, report.submit)

	def test_allowable_difference_tolerates_small_gap(self):
		report = self.ticked_report(z_no="Z-tol", allowable_difference=10)
		report.total_turnover = 240
		report.insert()
		report.submit()
		self.assertEqual(report.docstatus, 1)
		report.cancel()

	def test_submit_throws_when_receipts_issued_mismatch(self):
		report = self.ticked_report(z_no="Z-count", receipts_issued=2)
		report.insert()
		self.assertRaisesRegex(frappe.ValidationError, "Receipts Issued", report.submit)

	def test_submit_throws_when_invoice_already_linked(self):
		report = self.ticked_report(z_no="Z-linked")
		report.insert()
		frappe.db.set_value("Sales Invoice", self.standard_invoice.name, "efd_z_report", "SOMEWHERE-ELSE")
		self.addCleanup(
			frappe.db.set_value, "Sales Invoice", self.standard_invoice.name, "efd_z_report", None
		)
		self.assertRaisesRegex(frappe.ValidationError, "linked to EFD Z Report SOMEWHERE-ELSE", report.submit)
