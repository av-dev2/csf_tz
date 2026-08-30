# Copyright (c) 2025, Aakvatech and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from csf_tz.csftz_hooks.exchange_calculations import create_import_tracker
from csf_tz.tests.import_fixtures import (
	INR_BANK,
	INR_SUPPLIER,
	ORIGINAL_RATE,
	USD_SUPPLIER,
	get_tracker,
	make_foreign_purchase_invoice,
	make_purchase_invoice,
	make_supplier_payment,
	set_import_settings,
)

PAYMENT_RATE = 2600.0


class TestForeignImportTransaction(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_import_settings()

	def test_automatic_tracker_creation_on_foreign_pi_submit(self):
		invoice = make_foreign_purchase_invoice(do_not_submit=True)
		self.assertIsNone(get_tracker(invoice.name))
		invoice.submit()
		tracker = get_tracker(invoice.name)
		self.assertEqual(tracker.supplier, USD_SUPPLIER)
		self.assertEqual(tracker.currency, "USD")
		self.assertEqual(tracker.original_exchange_rate, ORIGINAL_RATE)
		self.assertEqual(tracker.invoice_amount_foreign, invoice.grand_total)
		self.assertEqual(tracker.invoice_amount_base, invoice.base_grand_total)
		self.assertEqual(tracker.status, "Active")
		self.assertEqual(tracker.docstatus, 1)

	def test_no_tracker_creation_for_base_currency_pi(self):
		invoice = make_purchase_invoice(supplier=INR_SUPPLIER, currency="INR", rate=100)
		self.assertIsNone(get_tracker(invoice.name))

	def test_payment_entry_linking_and_exchange_calculation(self):
		invoice = make_foreign_purchase_invoice()
		payment = make_supplier_payment(invoice.name, 500, PAYMENT_RATE)
		tracker = get_tracker(invoice.name)
		self.assertEqual(len(tracker.payments), 1)
		row = tracker.payments[0]
		self.assertEqual(row.payment_entry, payment.name)
		self.assertEqual(row.payment_amount_foreign, 500)
		self.assertEqual(row.payment_exchange_rate, PAYMENT_RATE)
		self.assertEqual(row.exchange_difference, 500 * (PAYMENT_RATE - ORIGINAL_RATE))
		self.assertEqual(row.journal_entry_created, 0)
		difference = tracker.exchange_differences[0]
		self.assertEqual(difference.reference_type, "Payment Entry")
		self.assertEqual(difference.reference_name, payment.name)
		self.assertEqual(difference.difference_type, "Gain")
		self.assertEqual(difference.amount, 50000)
		self.assertEqual(tracker.status, "Active")
		self.assertEqual(
			frappe.db.get_value("Payment Entry", payment.name, "foreign_import_tracker"), tracker.name
		)

	def test_status_change_to_completed_on_full_payment(self):
		invoice = make_foreign_purchase_invoice()
		make_supplier_payment(invoice.name, 1000, PAYMENT_RATE)
		self.assertEqual(get_tracker(invoice.name).status, "Completed")

	def test_exchange_loss_calculation(self):
		invoice = make_foreign_purchase_invoice()
		make_supplier_payment(invoice.name, 500, 2400)
		tracker = get_tracker(invoice.name)
		self.assertEqual(tracker.payments[0].exchange_difference, -50000)
		self.assertEqual(tracker.exchange_differences[0].difference_type, "Loss")
		self.assertEqual(tracker.exchange_differences[0].amount, 50000)

	def test_tracker_cancellation_on_pi_cancel(self):
		invoice = make_foreign_purchase_invoice()
		self.assertEqual(get_tracker(invoice.name).docstatus, 1)
		invoice.cancel()
		tracker = get_tracker(invoice.name)
		self.assertEqual(tracker.docstatus, 2)
		self.assertEqual(tracker.status, "Cancelled")

	def test_currency_validation(self):
		invoice = make_purchase_invoice(supplier=INR_SUPPLIER, currency="INR", rate=100)
		tracker = frappe.get_doc(
			{
				"doctype": "Foreign Import Transaction",
				"purchase_invoice": invoice.name,
				"supplier": INR_SUPPLIER,
				"currency": "INR",
				"company": "_Test Company",
				"transaction_date": nowdate(),
			}
		)
		with self.assertRaises(frappe.ValidationError):
			tracker.insert()

	def test_totals_calculation(self):
		invoice = make_foreign_purchase_invoice()
		tracker = get_tracker(invoice.name)
		tracker.add_exchange_difference("Purchase Invoice", invoice.name, "Gain", 25000, nowdate(), "gain")
		tracker.add_exchange_difference("Purchase Invoice", invoice.name, "Loss", 15000, nowdate(), "loss")
		self.assertEqual(tracker.total_gain_loss, 10000)
		self.assertEqual(tracker.net_difference, 10000)
		self.assertEqual(
			frappe.db.get_value("Foreign Import Transaction", tracker.name, "net_difference"), 10000
		)

	def test_exchange_summary_method(self):
		invoice = make_foreign_purchase_invoice()
		make_supplier_payment(invoice.name, 500, PAYMENT_RATE)
		summary = get_tracker(invoice.name).get_exchange_summary()
		self.assertEqual(summary["total_gain"], 50000)
		self.assertEqual(summary["total_loss"], 0)
		self.assertEqual(summary["payment_differences"], 50000)
		self.assertEqual(summary["lcv_differences"], 0)
		self.assertEqual(summary["manual_entries"], 0)
		self.assertEqual(summary["net_difference"], 50000)

	def test_no_duplicate_tracker_creation(self):
		invoice = make_foreign_purchase_invoice()
		create_import_tracker(invoice, "on_submit")
		self.assertEqual(frappe.db.count("Foreign Import Transaction", {"purchase_invoice": invoice.name}), 1)

	def test_payment_currency_mismatch_no_linking(self):
		invoice = make_purchase_invoice(
			supplier=INR_SUPPLIER, currency="EUR", conversion_rate=80, rate=100, qty=10
		)
		self.assertEqual(get_tracker(invoice.name).currency, "EUR")
		payment = make_supplier_payment(invoice.name, 200, 80, bank_account=INR_BANK)
		self.assertEqual(payment.paid_to_account_currency, "INR")
		tracker = get_tracker(invoice.name)
		self.assertEqual(tracker.payments, [])
		self.assertEqual(tracker.exchange_differences, [])
		self.assertFalse(frappe.db.get_value("Payment Entry", payment.name, "foreign_import_tracker"))

	def test_recalculate_differences_method(self):
		invoice = make_foreign_purchase_invoice()
		make_supplier_payment(invoice.name, 500, PAYMENT_RATE)
		tracker = get_tracker(invoice.name)
		tracker.exchange_differences = []
		tracker.save()
		tracker.reload()
		self.assertEqual(tracker.exchange_differences, [])
		self.assertEqual(tracker.total_gain_loss, 0)
		self.assertTrue(tracker.recalculate_differences())
		tracker.reload()
		self.assertEqual(len(tracker.exchange_differences), 1)
		self.assertEqual(tracker.exchange_differences[0].amount, 50000)
		self.assertEqual(tracker.total_gain_loss, 50000)
