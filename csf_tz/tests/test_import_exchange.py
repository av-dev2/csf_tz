from unittest.mock import patch

import frappe
from erpnext.stock.doctype.landed_cost_voucher.test_landed_cost_voucher import make_landed_cost_voucher
from frappe.tests import IntegrationTestCase

from csf_tz.csftz_hooks.exchange_calculations import (
	calculate_lcv_exchange_difference,
	create_exchange_difference_je,
	create_manual_exchange_entry,
	debug_payment_linking_issue,
	get_exchange_gain_loss_account,
	get_import_settings,
	get_supplier_payable_account,
	is_payable_account,
	manually_link_payment_to_tracker,
)
from csf_tz.tests.import_fixtures import (
	COMPANY,
	INR_BANK,
	INR_SUPPLIER,
	ORIGINAL_RATE,
	USD_SUPPLIER,
	get_tracker,
	make_foreign_purchase_invoice,
	make_plain_supplier_payment,
	make_supplier_payment,
	set_import_settings,
)

GAIN_LOSS_ACCOUNT = "Exchange Gain/Loss - _TC"
CREDITORS = "Creditors - _TC"


class TestImportExchangeDifferences(IntegrationTestCase):
	"""Journal entries, manual entries and LCV differences on a company-currency payable supplier."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_import_settings(auto_create_journal_entries=1)

	def make_import_with_payment(self, amount=500, rate=2600):
		invoice = make_foreign_purchase_invoice(supplier=INR_SUPPLIER)
		payment = make_supplier_payment(invoice.name, amount, rate)
		return invoice, payment

	def get_row(self, journal_entry, account):
		return next(row for row in journal_entry.accounts if row.account == account)

	def test_manual_link_creates_gain_journal_entry(self):
		invoice, payment = self.make_import_with_payment()
		tracker = get_tracker(invoice.name)
		self.assertEqual(tracker.payments, [])
		result = manually_link_payment_to_tracker(payment.name, tracker.name)
		self.assertIn("success", result)
		tracker.reload()
		row = tracker.payments[0]
		self.assertEqual(row.payment_amount_foreign, 500)
		self.assertEqual(row.payment_exchange_rate, 2600)
		self.assertEqual(row.exchange_difference, 50000)
		self.assertEqual(row.journal_entry_created, 1)
		difference = tracker.exchange_differences[0]
		self.assertEqual(difference.difference_type, "Gain")
		journal_entry = frappe.get_doc("Journal Entry", difference.journal_entry)
		self.assertEqual(journal_entry.docstatus, 1)
		self.assertEqual(journal_entry.voucher_type, "Exchange Gain Or Loss")
		payable = self.get_row(journal_entry, CREDITORS)
		self.assertEqual(
			(payable.party_type, payable.party, payable.debit), ("Supplier", INR_SUPPLIER, 50000)
		)
		self.assertEqual(self.get_row(journal_entry, GAIN_LOSS_ACCOUNT).credit, 50000)
		self.assertEqual(
			frappe.db.get_value("Payment Entry", payment.name, "foreign_import_tracker"), tracker.name
		)

	def test_loss_journal_entry_uses_configured_loss_account(self):
		loss_account = "_Test Exchange Gain/Loss - _TC"
		frappe.db.set_single_value("Foreign Import Settings", "default_exchange_loss_account", loss_account)
		self.addCleanup(
			frappe.db.set_single_value, "Foreign Import Settings", "default_exchange_loss_account", None
		)
		invoice, payment = self.make_import_with_payment(rate=2400)
		tracker = get_tracker(invoice.name)
		manually_link_payment_to_tracker(payment.name, tracker.name)
		tracker.reload()
		difference = tracker.exchange_differences[0]
		self.assertEqual(difference.difference_type, "Loss")
		self.assertEqual(difference.amount, 50000)
		journal_entry = frappe.get_doc("Journal Entry", difference.journal_entry)
		self.assertEqual(self.get_row(journal_entry, loss_account).debit, 50000)
		self.assertEqual(self.get_row(journal_entry, CREDITORS).credit, 50000)
		self.assertEqual(tracker.total_gain_loss, -50000)

	def test_manual_link_validations(self):
		invoice, payment = self.make_import_with_payment()
		tracker = get_tracker(invoice.name)
		other_tracker = get_tracker(make_foreign_purchase_invoice().name)
		self.assertIn(
			"doesn't match", manually_link_payment_to_tracker(payment.name, other_tracker.name)["error"]
		)
		manually_link_payment_to_tracker(payment.name, tracker.name)
		self.assertIn("already linked", manually_link_payment_to_tracker(payment.name, tracker.name)["error"])
		unrelated = make_plain_supplier_payment("_Test Supplier 2", 100)
		unrelated.insert()
		unrelated.submit()
		self.assertIn("No active trackers", manually_link_payment_to_tracker(unrelated.name)["error"])

	def test_manual_link_picks_open_supplier_tracker(self):
		invoice, payment = self.make_import_with_payment()
		result = manually_link_payment_to_tracker(payment.name)
		self.assertIn("success", result)
		linked = frappe.db.get_value("Payment Entry", payment.name, "foreign_import_tracker")
		self.assertEqual(frappe.db.get_value("Foreign Import Transaction", linked, "supplier"), INR_SUPPLIER)

	def test_payment_debug_report(self):
		invoice, payment = self.make_import_with_payment()
		info = debug_payment_linking_issue(payment.name)
		self.assertEqual(info["issues"], [])
		self.assertEqual(info["payment_details"]["party"], INR_SUPPLIER)
		tracker_info = next(t for t in info["potential_trackers"] if t["purchase_invoice"] == invoice.name)
		self.assertFalse(tracker_info["currency_match"])
		self.assertTrue(tracker_info["status_ok"])
		self.assertIn("Currency mismatch", tracker_info["issues"][0])
		manually_link_payment_to_tracker(payment.name, tracker_info["name"])
		tracker_info = next(
			t
			for t in debug_payment_linking_issue(payment.name)["potential_trackers"]
			if t["purchase_invoice"] == invoice.name
		)
		self.assertIn("Payment already linked to this tracker", tracker_info["issues"])
		self.assertIn("error", debug_payment_linking_issue("PE-DOES-NOT-EXIST"))

	def test_debug_report_flags_receive_payments(self):
		payment = make_plain_supplier_payment(INR_SUPPLIER, 100)
		payment.insert()
		info = debug_payment_linking_issue(payment.name)
		self.assertIn("Payment Entry not submitted (docstatus = 0)", info["issues"])

	def test_cancelling_payment_cancels_exchange_journal_entry(self):
		invoice, payment = self.make_import_with_payment()
		tracker = get_tracker(invoice.name)
		manually_link_payment_to_tracker(payment.name, tracker.name)
		journal_entry = get_tracker(invoice.name).exchange_differences[0].journal_entry
		frappe.get_doc("Payment Entry", payment.name).cancel()
		tracker.reload()
		self.assertEqual(tracker.payments, [])
		self.assertEqual(tracker.exchange_differences, [])
		self.assertEqual(frappe.db.get_value("Journal Entry", journal_entry, "docstatus"), 2)

	def test_manual_exchange_entry(self):
		invoice = make_foreign_purchase_invoice(supplier=INR_SUPPLIER)
		tracker = get_tracker(invoice.name)
		name = create_manual_exchange_entry(
			tracker.name, "Purchase Invoice", invoice.name, "Loss", 1200, "customs"
		)
		self.assertEqual(name, tracker.name)
		tracker.reload()
		difference = tracker.exchange_differences[0]
		self.assertEqual(
			(difference.difference_type, difference.amount, difference.remarks), ("Loss", 1200, "customs")
		)
		journal_entry = frappe.get_doc("Journal Entry", difference.journal_entry)
		self.assertEqual(self.get_row(journal_entry, GAIN_LOSS_ACCOUNT).debit, 1200)
		self.assertEqual(self.get_row(journal_entry, CREDITORS).credit, 1200)
		self.assertIn("Manual Loss Entry", journal_entry.user_remark)
		with self.assertRaisesRegex(frappe.ValidationError, "greater than 0"):
			create_manual_exchange_entry(tracker.name, "Purchase Invoice", invoice.name, "Gain", 0, "")

	def test_manual_exchange_entry_requires_submitted_tracker(self):
		invoice = make_foreign_purchase_invoice(supplier=INR_SUPPLIER)
		tracker = get_tracker(invoice.name)
		tracker.db_set("docstatus", 0)
		with self.assertRaisesRegex(frappe.ValidationError, "must be submitted"):
			create_manual_exchange_entry(tracker.name, "Purchase Invoice", invoice.name, "Gain", 10, "")

	def test_cancelling_tracker_cancels_journal_entries(self):
		invoice = make_foreign_purchase_invoice(supplier=INR_SUPPLIER)
		tracker = get_tracker(invoice.name)
		create_manual_exchange_entry(tracker.name, "Purchase Invoice", invoice.name, "Gain", 300, "manual")
		tracker.reload()
		journal_entry = tracker.exchange_differences[0].journal_entry
		tracker.cancel()
		self.assertEqual(frappe.db.get_value("Journal Entry", journal_entry, "docstatus"), 2)
		self.assertEqual(
			frappe.db.get_value("Foreign Import Transaction", tracker.name, "status"), "Cancelled"
		)

	def test_journal_entry_requires_gain_loss_account(self):
		invoice = make_foreign_purchase_invoice(supplier=INR_SUPPLIER)
		tracker = get_tracker(invoice.name)
		with (
			patch(
				"csf_tz.csftz_hooks.exchange_calculations.get_exchange_gain_loss_account", return_value=None
			),
			self.assertRaisesRegex(frappe.ValidationError, "not configured"),
		):
			create_exchange_difference_je(tracker, 100, "Gain", invoice, "test")

	def test_account_lookups(self):
		self.assertEqual(get_supplier_payable_account(USD_SUPPLIER, COMPANY), "_Test Payable USD - _TC")
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": "_Test Import Supplier",
				"supplier_group": "_Test Supplier Group",
			}
		).insert()
		self.assertEqual(get_supplier_payable_account(supplier.name, COMPANY), CREDITORS)
		self.assertTrue(is_payable_account(CREDITORS))
		self.assertFalse(is_payable_account(INR_BANK))
		self.assertEqual(get_exchange_gain_loss_account(COMPANY), GAIN_LOSS_ACCOUNT)

	def test_import_settings_get_default_company(self):
		frappe.db.set_single_value("Foreign Import Settings", "company", None)
		settings = get_import_settings(COMPANY)
		self.assertEqual(settings.company, COMPANY)
		self.assertEqual(frappe.db.get_single_value("Foreign Import Settings", "company"), COMPANY)

	def test_landed_cost_voucher_links_and_unlinks(self):
		invoice = make_foreign_purchase_invoice(supplier=INR_SUPPLIER, update_stock=1)
		voucher = make_landed_cost_voucher(
			receipt_document_type="Purchase Invoice", receipt_document=invoice.name, charges=50
		)
		item = voucher.items[0]
		self.assertEqual(item.custom_total_amount, item.amount + item.applicable_charges)
		self.assertEqual(voucher.custom_grand_total, sum(row.custom_total_amount for row in voucher.items))
		tracker = get_tracker(invoice.name)
		row = tracker.landed_cost_vouchers[0]
		self.assertEqual(row.landed_cost_voucher, voucher.name)
		self.assertEqual(row.lcv_amount_base, 50)
		self.assertEqual(row.allocated_to_items, 50)
		self.assertEqual(row.exchange_rate_used, 1)
		self.assertEqual(tracker.exchange_differences, [])
		voucher.cancel()
		tracker.reload()
		self.assertEqual(tracker.landed_cost_vouchers, [])

	def test_landed_cost_exchange_difference_with_rate(self):
		invoice = make_foreign_purchase_invoice(supplier=INR_SUPPLIER, update_stock=1)
		voucher = make_landed_cost_voucher(
			receipt_document_type="Purchase Invoice", receipt_document=invoice.name, charges=50
		)
		tracker = get_tracker(invoice.name)
		voucher_at_new_rate = frappe._dict(
			name=voucher.name,
			posting_date=voucher.posting_date,
			total_taxes_and_charges=50,
			conversion_rate=2600,
		)
		calculate_lcv_exchange_difference(tracker, voucher_at_new_rate)
		tracker.reload()
		difference = tracker.exchange_differences[0]
		self.assertEqual(difference.reference_type, "Landed Cost Voucher")
		self.assertEqual(difference.difference_type, "Loss")
		self.assertAlmostEqual(difference.amount, 50 - 50 / 2600 * ORIGINAL_RATE, places=2)
		self.assertTrue(difference.journal_entry)
		self.assertEqual(tracker.get_exchange_summary()["lcv_differences"], difference.amount)
		frappe.get_doc("Landed Cost Voucher", voucher.name).cancel()
		tracker.reload()
		self.assertEqual(tracker.exchange_differences, [])

	def test_landed_cost_tracking_can_be_disabled(self):
		frappe.db.set_single_value("Foreign Import Settings", "enable_lcv_exchange_tracking", 0)
		self.addCleanup(
			frappe.db.set_single_value, "Foreign Import Settings", "enable_lcv_exchange_tracking", 1
		)
		invoice = make_foreign_purchase_invoice(supplier=INR_SUPPLIER)
		tracker = get_tracker(invoice.name)
		calculate_lcv_exchange_difference(tracker, frappe._dict(name="LCV", conversion_rate=2600))
		self.assertEqual(tracker.exchange_differences, [])
