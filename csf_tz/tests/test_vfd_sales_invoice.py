import frappe
from frappe.tests import IntegrationTestCase

from csf_tz.tests.vfd_test_records import (
	EXEMPT_TEMPLATE,
	ITEM,
	NO_CODE_TEMPLATE,
	STANDARD_OTHER_TEMPLATE,
	STANDARD_TEMPLATE,
	STANDARD_ZERO_TEMPLATE,
	make_customer,
	make_item_tax_template,
	make_item_tax_templates,
	make_sales_invoice,
	make_vfd_providers,
	make_vfdplus_settings,
)
from csf_tz.vfd_support import sales_invoice as vfd


class TestVFDCustomer(IntegrationTestCase):
	def test_validate_cleans_tax_id_into_vfd_fields(self):
		customer = make_customer("_Test VFD TIN Customer", tax_id="123-456-789")
		self.assertEqual(customer.tax_id, "123456789")
		self.assertEqual(customer.vfd_cust_id_type, "1- TIN")
		self.assertEqual(customer.vfd_cust_id, "123456789")

	def test_validate_without_tax_id_uses_other_id_type(self):
		customer = make_customer("_Test VFD Other Customer", tax_id="")
		self.assertEqual(customer.tax_id, "")
		self.assertEqual(customer.vfd_cust_id_type, "6- Other")
		self.assertEqual(customer.vfd_cust_id, "999999999")


class TestVFDSalesInvoiceValidation(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_item_tax_templates()
		make_customer()

	def test_submit_sets_customer_id_from_customer(self):
		invoice = make_sales_invoice(submit=True)
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(invoice.vfd_cust_id, "123456789")
		self.assertEqual(invoice.vfd_cust_id_type, "1- TIN")
		self.assertEqual(invoice.vfd_status, "Not Sent")
		self.assertEqual(invoice.base_total_taxes_and_charges, 36)

	def test_exempt_item_with_zero_tax_submits(self):
		invoice = make_sales_invoice(item_tax_template=EXEMPT_TEMPLATE, submit=True)
		self.assertEqual(invoice.base_total_taxes_and_charges, 0)

	def test_non_vfd_invoice_skips_validation(self):
		invoice = make_sales_invoice(
			item_tax_template=None, with_taxes=False, is_not_vfd_invoice=1, submit=True
		)
		self.assertEqual(invoice.docstatus, 1)

	def test_throws_when_net_total_is_zero(self):
		invoice = make_sales_invoice(items=[{"item_code": ITEM, "qty": 1, "discount_percentage": 100}])
		self.assertEqual(invoice.base_net_total, 0)
		self.assertRaisesRegex(frappe.ValidationError, "Base net amount is zero", invoice.submit)

	def test_throws_when_taxes_missing(self):
		invoice = make_sales_invoice(item_tax_template=None, with_taxes=False)
		self.assertRaisesRegex(frappe.ValidationError, "Taxes not set correctly", invoice.submit)

	def test_throws_without_item_tax_template(self):
		invoice = make_sales_invoice(item_tax_template=None)
		self.assertRaisesRegex(frappe.ValidationError, "Item Taxes Template not set", invoice.submit)

	def test_uses_item_default_tax_template(self):
		frappe.db.set_value("Item", ITEM, "default_tax_template", STANDARD_TEMPLATE)
		self.addCleanup(frappe.db.set_value, "Item", ITEM, "default_tax_template", None)
		invoice = make_sales_invoice(item_tax_template=None, submit=True)
		self.assertEqual(invoice.items[0].item_tax_template, STANDARD_TEMPLATE)

	def test_throws_for_standard_item_without_18pct_tax(self):
		invoice = make_sales_invoice(item_tax_template=STANDARD_ZERO_TEMPLATE)
		self.assertRaisesRegex(frappe.ValidationError, "Standard Rate item", invoice.submit)

	def test_throws_for_other_tax_rate(self):
		invoice = make_sales_invoice(item_tax_template=STANDARD_OTHER_TEMPLATE)
		self.assertRaisesRegex(frappe.ValidationError, "Other Tax item", invoice.submit)

	def test_throws_for_exempt_item_with_18pct_tax(self):
		template = make_item_tax_template("VFD Exempt Wrong - _TC", "5- Exempt", 18)
		invoice = make_sales_invoice(item_tax_template=template)
		self.assertRaisesRegex(frappe.ValidationError, "Non Standard Rate item", invoice.submit)

	def test_throws_when_template_has_no_vfd_taxcode(self):
		invoice = make_sales_invoice(item_tax_template=NO_CODE_TEMPLATE)
		self.assertRaisesRegex(frappe.ValidationError, "VFD Tax Code not setup", invoice.submit)

	def test_vat_enabled_vfdplus_only_warns_for_standard_item(self):
		make_vfd_providers()
		make_vfdplus_settings()
		frappe.db.set_value("VFDPlus Settings", "_Test Company", "vat_enabled", 1)
		invoice = make_sales_invoice(item_tax_template=STANDARD_ZERO_TEMPLATE, submit=True)
		self.assertEqual(invoice.docstatus, 1)

	def test_throws_when_customer_id_type_missing(self):
		customer = make_customer("_Test VFD No Type Customer")
		frappe.db.set_value("Customer", customer.name, "vfd_cust_id_type", "")
		invoice = make_sales_invoice(customer=customer.name)
		invoice.vfd_cust_id_type = ""
		self.assertRaisesRegex(frappe.ValidationError, "VFD Customer ID Type", invoice.submit)

	def test_get_customer_id_info_for_customer_without_id(self):
		customer = make_customer("_Test VFD No ID Customer")
		frappe.db.set_value("Customer", customer.name, {"vfd_cust_id": "", "vfd_cust_id_type": ""})
		info = vfd.get_customer_id_info(customer.name)
		self.assertEqual(info, {"cust_id": "", "cust_id_type": 6, "mobile_no": "2550712345678"})


class TestVFDSalesInvoiceCancel(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_item_tax_templates()
		make_customer()

	def test_cancel_blocked_after_vfd_sent(self):
		invoice = make_sales_invoice(submit=True)
		invoice.db_set("vfd_rctvnum", "ABC123")
		invoice.reload()
		self.assertRaisesRegex(frappe.ValidationError, "already sent to TRA", invoice.cancel)

	def test_cancel_allowed_before_vfd_sent(self):
		invoice = make_sales_invoice(submit=True)
		invoice.cancel()
		self.assertEqual(invoice.docstatus, 2)


class TestVFDSalesInvoiceHelpers(IntegrationTestCase):
	def test_get_item_taxcode_throws_without_template(self):
		self.assertRaisesRegex(frappe.ValidationError, "Item Taxes Template not set$", vfd.get_item_taxcode)
		self.assertRaisesRegex(frappe.ValidationError, "for item X$", vfd.get_item_taxcode, item_code="X")
		self.assertRaisesRegex(
			frappe.ValidationError,
			"for item X in invoice INV",
			vfd.get_item_taxcode,
			item_code="X",
			invoice_name="INV",
		)

	def test_get_item_taxcode_reads_template(self):
		make_item_tax_templates()
		self.assertEqual(vfd.get_item_taxcode(STANDARD_TEMPLATE), 1)
		self.assertEqual(vfd.get_item_taxcode(EXEMPT_TEMPLATE), 5)

	def test_get_item_inclusive_amount(self):
		exclusive = frappe._dict(base_net_amount=100, base_amount=100, item_tax_rate='{"VAT": 18}')
		self.assertEqual(vfd.get_item_inclusive_amount(exclusive), 118)
		zero_rate = frappe._dict(base_net_amount=100, base_amount=100, item_tax_rate='{"VAT": 0}')
		self.assertEqual(vfd.get_item_inclusive_amount(zero_rate), 100)
		no_rate = frappe._dict(base_net_amount=100, base_amount=100, item_tax_rate="{}")
		self.assertEqual(vfd.get_item_inclusive_amount(no_rate), 100)
		inclusive = frappe._dict(base_net_amount=84.75, base_amount=100, item_tax_rate='{"VAT": 18}')
		self.assertEqual(vfd.get_item_inclusive_amount(inclusive), 100)

	def test_text_cleaners(self):
		self.assertEqual(vfd.remove_special_characters("A-1 (b)!"), "A1 b")
		self.assertEqual(vfd.remove_all_except_numbers("+255 (0)71"), "255071")
		self.assertEqual(vfd.remove_all_except_numbers(None), "")

	def test_tax_breakup_is_empty_without_taxes(self):
		self.assertIsNone(vfd.get_itemised_tax_breakup_html(frappe._dict(taxes=[])))
