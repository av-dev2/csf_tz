from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from csf_tz.csf_tz.doctype.foreign_import_transaction.foreign_import_transaction import (
	ForeignImportTransaction,
)
from csf_tz.csftz_hooks.exchange_calculations import update_pending_transactions
from csf_tz.tests.import_fixtures import (
	COMPANY,
	INR_BANK,
	INR_SUPPLIER,
	ORIGINAL_RATE,
	get_tracker,
	make_foreign_purchase_invoice,
	make_purchase_invoice,
	make_supplier_payment,
	set_import_settings,
)


class TestForeignImportTracker(IntegrationTestCase):
	"""Tracker behaviour beyond the DocType test: payment sides, unlinking, scheduler."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_import_settings()

	def test_invoice_keeps_tracker_reference_in_memory(self):
		invoice = make_foreign_purchase_invoice()
		tracker = get_tracker(invoice.name)
		self.assertEqual(invoice.foreign_import_tracker, tracker.name)
		self.assertEqual(
			frappe.db.get_value("Purchase Invoice", invoice.name, "foreign_import_tracker"), tracker.name
		)

	def test_payment_from_company_currency_bank_uses_supplier_side(self):
		invoice = make_foreign_purchase_invoice()
		payment = make_supplier_payment(invoice.name, 400, 2400, bank_account=INR_BANK)
		tracker = get_tracker(invoice.name)
		row = tracker.payments[0]
		self.assertEqual(row.payment_entry, payment.name)
		self.assertEqual(row.payment_amount_foreign, 400)
		self.assertEqual(row.payment_exchange_rate, 2400)
		self.assertEqual(row.exchange_difference, -40000)
		self.assertEqual(tracker.exchange_differences[0].difference_type, "Loss")
		self.assertEqual(tracker.exchange_differences[0].amount, 40000)
		self.assertEqual(tracker.total_gain_loss, -40000)
		self.assertEqual(payment.foreign_import_tracker, tracker.name)

	def test_payment_at_original_rate_records_no_difference(self):
		invoice = make_foreign_purchase_invoice()
		make_supplier_payment(invoice.name, 300, ORIGINAL_RATE)
		tracker = get_tracker(invoice.name)
		self.assertEqual(len(tracker.payments), 1)
		self.assertEqual(tracker.exchange_differences, [])
		self.assertEqual(tracker.status, "Active")

	def test_cancelling_payment_unlinks_it(self):
		invoice = make_foreign_purchase_invoice()
		payment = make_supplier_payment(invoice.name, 500, 2600)
		self.assertEqual(len(get_tracker(invoice.name).payments), 1)
		payment.cancel()
		tracker = get_tracker(invoice.name)
		self.assertEqual(tracker.payments, [])
		self.assertEqual(tracker.exchange_differences, [])
		self.assertEqual(tracker.total_gain_loss, 0)

	def test_tracker_validations(self):
		invoice = make_purchase_invoice(supplier=INR_SUPPLIER, currency="INR", rate=100)
		tracker = frappe.get_doc(
			{
				"doctype": "Foreign Import Transaction",
				"purchase_invoice": invoice.name,
				"supplier": INR_SUPPLIER,
				"currency": "INR",
				"company": COMPANY,
				"transaction_date": nowdate(),
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "foreign currency"):
			tracker.insert()
		tracker.currency = None
		with self.assertRaisesRegex(frappe.ValidationError, "Currency is required"):
			tracker.validate_currency()

	def test_draft_tracker_totals_and_summary(self):
		invoice = make_purchase_invoice(
			supplier=INR_SUPPLIER, currency="EUR", conversion_rate=80, rate=100, do_not_submit=True
		)
		tracker = frappe.get_doc(
			{
				"doctype": "Foreign Import Transaction",
				"purchase_invoice": invoice.name,
				"supplier": INR_SUPPLIER,
				"currency": "EUR",
				"company": COMPANY,
				"transaction_date": nowdate(),
				"invoice_amount_foreign": 1000,
			}
		)
		for difference_type, amount in (("Gain", 300), ("Loss", 100)):
			tracker.append(
				"exchange_differences",
				{
					"reference_type": "Purchase Invoice",
					"reference_name": invoice.name,
					"difference_type": difference_type,
					"amount": amount,
					"posting_date": nowdate(),
				},
			)
		tracker.insert()
		self.assertEqual(tracker.status, "Draft")
		self.assertEqual(tracker.total_gain_loss, 200)
		self.assertEqual(tracker.net_difference, 200)
		summary = tracker.get_exchange_summary()
		self.assertEqual(summary["total_gain"], 300)
		self.assertEqual(summary["total_loss"], 100)
		self.assertEqual(summary["manual_entries"], 400)
		self.assertEqual(summary["net_difference"], 200)

	def test_update_pending_transactions_refreshes_status(self):
		invoice = make_foreign_purchase_invoice()
		make_supplier_payment(invoice.name, 1000, ORIGINAL_RATE)
		tracker = get_tracker(invoice.name)
		self.assertEqual(tracker.status, "Completed")
		tracker.db_set("status", "Active")
		update_pending_transactions()
		self.assertEqual(
			frappe.db.get_value("Foreign Import Transaction", tracker.name, "status"), "Completed"
		)

	def test_update_pending_transactions_logs_errors(self):
		make_foreign_purchase_invoice()
		with (
			patch.object(ForeignImportTransaction, "calculate_totals", side_effect=Exception("boom")),
			patch("frappe.log_error") as log_error,
		):
			update_pending_transactions()
		self.assertTrue(log_error.called)
		self.assertIn("boom", log_error.call_args.args[0])
