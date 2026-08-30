import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from csf_tz.custom_api import get_item_info
from csf_tz.tests.custom_api_helpers import (
	WAREHOUSE,
	add_stock,
	disable_db_commit,
	make_sales_invoice,
	make_test_item,
	set_csf_settings,
)


class TestValidateGrandTotal(IntegrationTestCase):
	"""before_submit hook: POS payments must match the invoice total."""

	def setUp(self):
		disable_db_commit(self)
		set_csf_settings(validate_grand_total_vs_payment_amount_on_sales_invoice=1)

	def pos_invoice(self, paid_amount):
		invoice = make_sales_invoice(is_pos=1, rate=100, do_not_save=True)
		invoice.append(
			"payments", {"mode_of_payment": "Cash", "account": "Cash - _TC", "amount": paid_amount}
		)
		return invoice.insert()

	def test_throws_when_payment_differs_from_total(self):
		invoice = self.pos_invoice(50)
		self.assertRaises(frappe.ValidationError, invoice.submit)

	def test_submits_when_payment_matches_total(self):
		invoice = self.pos_invoice(100)
		invoice.submit()
		self.assertEqual(invoice.docstatus, 1)

	def test_skipped_when_setting_is_off(self):
		set_csf_settings(validate_grand_total_vs_payment_amount_on_sales_invoice=0)
		invoice = self.pos_invoice(50)
		invoice.submit()
		self.assertEqual(invoice.docstatus, 1)


class TestValidateNetRate(IntegrationTestCase):
	"""on_submit hook: selling below last purchase or valuation rate is blocked."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.purchase_item = make_test_item("_CSF Net Rate Purchase Item", last_purchase_rate=200)
		cls.valuation_item = make_test_item("_CSF Net Rate Valuation Item")
		add_stock(cls.valuation_item.name, qty=10, rate=150)

	def setUp(self):
		disable_db_commit(self)
		set_csf_settings(validate_net_rate=1)

	def test_throws_below_last_purchase_rate(self):
		invoice = make_sales_invoice(item_code=self.purchase_item.name, rate=100, do_not_submit=True)
		self.assertRaisesRegex(frappe.ValidationError, "last purchase rate", invoice.submit)

	def test_throws_below_valuation_rate(self):
		invoice = make_sales_invoice(item_code=self.valuation_item.name, rate=100, do_not_submit=True)
		self.assertRaisesRegex(frappe.ValidationError, "valuation rate", invoice.submit)

	def test_passes_above_reference_rates(self):
		invoice = make_sales_invoice(item_code=self.valuation_item.name, rate=300)
		self.assertEqual(invoice.docstatus, 1)

	def test_override_flag_skips_row(self):
		invoice = make_sales_invoice(item_code=self.purchase_item.name, rate=100, allow_override_net_rate=1)
		self.assertEqual(invoice.docstatus, 1)

	def test_skipped_when_setting_is_off(self):
		set_csf_settings(validate_net_rate=0)
		invoice = make_sales_invoice(item_code=self.purchase_item.name, rate=100)
		self.assertEqual(invoice.docstatus, 1)


class TestCalculatePriceReduction(IntegrationTestCase):
	"""validate hook: price_reduction sums qty * discount_amount."""

	def setUp(self):
		disable_db_commit(self)

	def test_price_reduction_is_set(self):
		invoice = make_sales_invoice(qty=3, price_list_rate=100, rate=90, do_not_submit=True)
		row = invoice.items[0]
		self.assertEqual(row.discount_amount, 10)
		self.assertEqual(invoice.price_reduction, 30)


class TestBatchSplitting(IntegrationTestCase):
	"""before_insert hook: rows are split per available batch when update_stock is set."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_test_item("_CSF Batch Split Item", has_batch_no=1, create_new_batch=0)
		cls.batches = []
		for suffix, qty in (("A", 6), ("B", 10)):
			batch = frappe.get_doc(
				doctype="Batch",
				item=cls.item.name,
				batch_id=f"_CSF-SPLIT-{suffix}",
				expiry_date=add_days(nowdate(), 30),
			).insert()
			add_stock(cls.item.name, qty=qty, rate=10, batch_no=batch.name)
			cls.batches.append(batch.name)

	def setUp(self):
		disable_db_commit(self)
		set_csf_settings(allow_batch_splitting=1)
		frappe.db.set_single_value("Selling Settings", "allow_multiple_items", 1)

	def batch_invoice(self, rows, **args):
		return make_sales_invoice(
			update_stock=1,
			set_warehouse=WAREHOUSE,
			item_code=self.item.name,
			rate=50,
			rows=[dict(qty=qty, stock_qty=qty) for qty in rows],
			**args,
		)

	def test_single_row_is_split_across_batches(self):
		invoice = self.batch_invoice([10], do_not_submit=True)
		allocations = {row.batch_no: row.qty for row in invoice.items}
		self.assertEqual(allocations, {self.batches[0]: 6, self.batches[1]: 4})

	def test_submit_consumes_batches_and_item_info_reflects_it(self):
		self.batch_invoice([8])
		info = {row["batch_no"]: row["actual_qty"] for row in get_item_info(self.item.name)}
		self.assertEqual(info.get(self.batches[0]), 0)
		self.assertEqual(info.get(self.batches[1]), 8)

	def test_duplicate_rows_share_batches(self):
		invoice = self.batch_invoice([3, 5], do_not_submit=True)
		self.assertEqual(sum(row.qty for row in invoice.items), 8)
		self.assertTrue(all(row.batch_no for row in invoice.items))

	def test_throws_when_stock_is_short(self):
		self.assertRaisesRegex(
			frappe.ValidationError, "not enough", self.batch_invoice, [50], do_not_submit=True
		)

	def test_throws_without_source_warehouse(self):
		invoice = make_sales_invoice(
			update_stock=1, item_code=self.item.name, rows=[dict(qty=1, stock_qty=1)], do_not_save=True
		)
		self.assertRaisesRegex(frappe.ValidationError, "source warehouse", invoice.insert)

	def test_setting_off_keeps_rows(self):
		set_csf_settings(allow_batch_splitting=0)
		invoice = self.batch_invoice([10], do_not_submit=True)
		self.assertEqual(len(invoice.items), 1)
		self.assertFalse(invoice.items[0].batch_no)

	def test_return_and_no_update_stock_are_skipped(self):
		invoice = make_sales_invoice(
			item_code=self.item.name, rows=[dict(qty=10, stock_qty=10)], do_not_submit=True
		)
		self.assertEqual(len(invoice.items), 1)
