"""Builders shared by the payments and foreign import tests."""

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice

COMPANY = "_Test Company"
USD_SUPPLIER = "_Test Supplier USD"
INR_SUPPLIER = "_Test Supplier"
USD_BANK = "_Test Bank USD - _TC"
INR_BANK = "_Test Bank - _TC"
ORIGINAL_RATE = 2500.0


def set_import_settings(**values):
	"""Configure Foreign Import Settings and allow foreign invoices on company-currency party accounts."""
	frappe.db.set_single_value(
		"Accounts Settings", "allow_multi_currency_invoices_against_single_party_account", 1
	)
	settings = {
		"company": COMPANY,
		"exchange_difference_threshold": 0.01,
		"auto_create_journal_entries": 0,
		"enable_lcv_exchange_tracking": 1,
		"default_exchange_gain_account": None,
		"default_exchange_loss_account": None,
	}
	settings.update(values)
	for field, value in settings.items():
		frappe.db.set_single_value("Foreign Import Settings", field, value)


def make_foreign_purchase_invoice(**args):
	args.setdefault("supplier", USD_SUPPLIER)
	args.setdefault("currency", "USD")
	args.setdefault("conversion_rate", ORIGINAL_RATE)
	args.setdefault("rate", 100)
	args.setdefault("qty", 10)
	return make_purchase_invoice(**args)


def get_tracker(purchase_invoice):
	name = frappe.db.get_value("Foreign Import Transaction", {"purchase_invoice": purchase_invoice}, "name")
	return frappe.get_doc("Foreign Import Transaction", name) if name else None


def make_supplier_payment(purchase_invoice, amount, rate, bank_account=USD_BANK):
	"""Pay `amount` in the invoice currency at `rate`, from the given bank account."""
	invoice_currency = frappe.db.get_value("Purchase Invoice", purchase_invoice, "currency")
	payment = get_payment_entry("Purchase Invoice", purchase_invoice, bank_account=bank_account)
	if payment.paid_from_account_currency == invoice_currency:
		payment.paid_amount, payment.source_exchange_rate = amount, rate
	else:
		payment.paid_amount, payment.source_exchange_rate = amount * rate, 1
	if payment.paid_to_account_currency == invoice_currency:
		payment.received_amount, payment.target_exchange_rate = amount, rate
	else:
		payment.received_amount, payment.target_exchange_rate = amount * rate, 1
	reference = payment.references[0]
	reference.allocated_amount = min(payment.received_amount, reference.outstanding_amount)
	payment.reference_no = "TEST-REF"
	payment.reference_date = frappe.utils.nowdate()
	payment.insert()
	payment.submit()
	return payment


def make_plain_supplier_payment(supplier, amount, **values):
	payment = frappe.new_doc("Payment Entry")
	payment.update(
		{
			"payment_type": "Pay",
			"party_type": "Supplier",
			"party": supplier,
			"company": COMPANY,
			"paid_from": INR_BANK,
			"paid_amount": amount,
			"received_amount": amount,
			"source_exchange_rate": 1,
			"target_exchange_rate": 1,
			"reference_no": "TEST-REF",
			"reference_date": frappe.utils.nowdate(),
		}
	)
	payment.update(values)
	return payment
