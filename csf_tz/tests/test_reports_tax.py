import frappe
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime, today

from csf_tz.tests.report_fixtures import COMPANY, as_dicts, date_range, receive_stock, run_report, sell_stock


def make_efd_device():
	return frappe.get_doc(
		{
			"doctype": "Electronic Fiscal Device",
			"type": "Electronic Fiscal Printer (EFP)",
			"serial_no": "EFD-TEST-1",
			"location": "Test Location",
			"make": "TestMake",
			"model": "M1",
		}
	).insert()


def make_efd_z_report(device, z_no, sales_invoice=None):
	invoices = []
	if sales_invoice:
		invoices.append(
			{
				"invoice_number": sales_invoice.name,
				"invoice_date": sales_invoice.posting_date,
				"invoice_amount": sales_invoice.grand_total,
				"amt_excl_vat": sales_invoice.net_total,
				"vat": sales_invoice.total_taxes_and_charges,
				"amt_ex__sr": 0,
				"include": 1,
			}
		)
	return frappe.get_doc(
		{
			"doctype": "EFD Z Report",
			"electronic_fiscal_device": device.name,
			"z_no": z_no,
			"receipts_issued": len(invoices),
			"z_report_date_time": now_datetime(),
			"efd_z_report_invoices": invoices,
		}
	).insert()


def make_taxed_purchase_invoice():
	invoice = make_purchase_invoice(qty=5, rate=50, posting_date=today(), do_not_save=1)
	invoice.bill_no = "SUP-INV-1"
	invoice.bill_date = today()
	invoice.append(
		"taxes",
		{
			"charge_type": "On Net Total",
			"account_head": "_Test Account VAT - _TC",
			"description": "VAT",
			"rate": 18,
			"cost_center": "_Test Cost Center - _TC",
		},
	)
	invoice.insert()
	invoice.submit()
	return invoice


class TestTaxReports(IntegrationTestCase):
	"""Runs the VAT, withholding and excise reports of csf_tz."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		receive_stock(qty=20, rate=100)
		cls.sales_invoice = sell_stock(qty=2, rate=500)
		cls.purchase_invoice = make_taxed_purchase_invoice()
		cls.efd_device = make_efd_device()

	def test_output_vat_reconciliation(self):
		efd_report = make_efd_z_report(self.efd_device, "Z-1", self.sales_invoice)
		columns, rows = run_report("Output VAT Reconciliation", {"efd_report": efd_report.name})
		rows = as_dicts(columns, rows)
		self.assertEqual(rows[0]["details"], "Sales - Sales Returns")
		self.assertEqual(rows[1]["details"], self.sales_invoice.name)
		self.assertEqual(rows[1]["invoice_currency"], "INR")
		self.assertEqual(rows[-1]["details"], "Sales as VAT Returns")

	def test_output_vat_reconciliation_without_invoices(self):
		columns, rows = run_report("Output VAT Reconciliation", {"efd_report": "EFD-MISSING"})
		self.assertEqual(rows, [])

	def test_withholding_tax_payment_summary_needs_lease_fields(self):
		with self.assertRaises(frappe.ValidationError):
			run_report("Withholding Tax Payment Summary", {"rental": "Commercial Rent"})

	def test_withholding_tax_summary_on_sales(self):
		columns, rows = run_report("Withholding Tax Summary on Sales", date_range())
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_withholding_tax_upload(self):
		columns, rows = run_report("Withholding Tax Upload", {})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_itx_230_withholding_tax_statement(self):
		columns, rows = run_report("ITX 230.01.E – Withholding Tax Statement", date_range())
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_tra_input_vat_returns_efiling(self):
		columns, rows = run_report("TRA Input VAT Returns eFiling", date_range())
		rows = as_dicts(columns, rows)
		self.assertEqual(len(rows), 1, rows)
		self.assertEqual(rows[0]["tax_invoice_number"], "SUP-INV-1")
		self.assertEqual(rows[0]["vat_amt"], self.purchase_invoice.total_taxes_and_charges)

	def test_vat_efiling_returns(self):
		columns, rows = run_report("VAT eFiling Returns", {"company": COMPANY, **date_range()})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)

	def test_excise_duty_reports(self):
		for report_name in ("Excise Duty Report", "Excise Duty Detailed Report"):
			columns, rows = run_report(report_name, date_range())
			self.assertTrue(columns, report_name)
			self.assertIsInstance(rows, list, report_name)

	def test_excise_duty_stock(self):
		columns, rows = run_report("Excise Duty Stock", {"company": COMPANY, **date_range()})
		self.assertTrue(columns)
		self.assertIsInstance(rows, list)
