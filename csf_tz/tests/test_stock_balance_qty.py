from unittest.mock import patch

import frappe
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, nowtime, today

from csf_tz.csftz_hooks import items_revaluation
from csf_tz.utils import fix_balance_qty

COMPANY = "_Test Company"
WAREHOUSE = "_Test Warehouse - _TC"


def sle_of(stock_entry):
	return frappe.db.get_value(
		"Stock Ledger Entry", {"voucher_no": stock_entry.name, "is_cancelled": 0}, "name"
	)


def corrupt_balance(test_case, sle_name, wrong_qty=99):
	correct_qty = frappe.db.get_value("Stock Ledger Entry", sle_name, "qty_after_transaction")
	frappe.db.set_value(
		"Stock Ledger Entry", sle_name, "qty_after_transaction", wrong_qty, update_modified=False
	)
	test_case.addCleanup(
		frappe.db.set_value,
		"Stock Ledger Entry",
		sle_name,
		"qty_after_transaction",
		correct_qty,
		update_modified=False,
	)


class TestIncorrectBalanceDetection(UnitTestCase):
	def rows(self, *specs):
		return [
			frappe._dict(
				voucher_type=voucher_type, actual_qty=actual, qty_after_transaction=after, batch_no=batch
			)
			for voucher_type, actual, after, batch in specs
		]

	def test_consistent_rows_return_nothing(self):
		rows = self.rows(("Stock Entry", 10, 10, None), ("Stock Entry", -4, 6, None))
		self.assertIsNone(items_revaluation.get_incorrect_data(rows))

	def test_first_wrong_row_is_returned_with_difference(self):
		rows = self.rows(
			("Stock Entry", 10, 10, None), ("Stock Entry", -4, 9, None), ("Stock Entry", 1, 10, None)
		)
		row = items_revaluation.get_incorrect_data(rows)
		self.assertEqual(row.qty_after_transaction, 9)
		self.assertEqual(row.expected_balance_qty, 6)
		self.assertEqual(row.differnce, 3)

	def test_stock_reconciliation_resets_balance(self):
		rows = self.rows(
			("Stock Entry", 10, 10, None), ("Stock Reconciliation", 0, 5, None), ("Stock Entry", 1, 6, None)
		)
		self.assertIsNone(items_revaluation.get_incorrect_data(rows))

	def test_batch_reconciliation_does_not_reset_balance(self):
		rows = self.rows(("Stock Entry", 10, 10, None), ("Stock Reconciliation", 0, 5, "BATCH-1"))
		self.assertEqual(items_revaluation.get_incorrect_data(rows).differnce, 5)

	def test_validate_data_collects_one_row_per_item_warehouse(self):
		grouped = {
			("A", "W"): self.rows(("Stock Entry", 10, 10, None)),
			("B", "W"): self.rows(("Stock Entry", 10, 12, None)),
		}
		self.assertEqual(len(items_revaluation.validate_data(grouped)), 1)


class TestItemsRevaluation(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_item(properties={"is_stock_item": 1, "valuation_rate": 10}).name
		cls.receipt = make_stock_entry(
			item_code=cls.item, qty=10, to_warehouse=WAREHOUSE, rate=10, company=COMPANY
		)
		cls.issue = make_stock_entry(item_code=cls.item, qty=4, from_warehouse=WAREHOUSE, company=COMPANY)

	def item_filters(self):
		return {"item_code": self.item, "warehouse": WAREHOUSE}

	def test_consistent_ledger_has_no_incorrect_rows(self):
		self.assertEqual(items_revaluation.get_data(self.item_filters()), [])

	def test_ledger_entries_are_ordered_by_posting_datetime(self):
		entries = items_revaluation.get_stock_ledger_entries(frappe._dict(self.item_filters()))
		self.assertEqual([row.voucher_no for row in entries], [self.receipt.name, self.issue.name])

	def test_wrong_balance_is_reported(self):
		corrupt_balance(self, sle_of(self.issue))
		rows = items_revaluation.get_data(self.item_filters())
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].voucher_no, self.issue.name)
		self.assertEqual(rows[0].expected_balance_qty, 6)
		self.assertEqual(rows[0].differnce, 93)

	def test_company_filter_excludes_other_companies(self):
		corrupt_balance(self, sle_of(self.issue))
		self.assertEqual(
			items_revaluation.get_data({"company": "_Test Company 1", "item_code": self.item}), []
		)

	def test_process_creates_repost_and_fixes_balance(self):
		corrupt_balance(self, sle_of(self.issue))
		with patch.object(frappe.db, "commit"):
			items_revaluation.process_incorrect_balance_qty()
		repost = frappe.get_last_doc("Repost Item Valuation", filters={"voucher_no": self.issue.name})
		self.assertEqual(
			(repost.based_on, repost.docstatus, repost.allow_negative_stock), ("Transaction", 1, 1)
		)
		self.assertEqual(repost.voucher_type, "Stock Entry")
		self.assertEqual(
			frappe.db.get_value("Stock Ledger Entry", sle_of(self.issue), "qty_after_transaction"), 6
		)

	def test_process_does_nothing_for_consistent_ledger(self):
		before = frappe.db.count("Repost Item Valuation")
		with patch.object(frappe.db, "commit"):
			items_revaluation.process_incorrect_balance_qty()
		self.assertEqual(frappe.db.count("Repost Item Valuation"), before)


class TestHasCorrectBalanceQty(UnitTestCase):
	def previous(self, qty):
		return frappe._dict(item_code="A", warehouse="W", qty_after_transaction=qty)

	def sle(self, actual, after, voucher_type="Stock Entry", item_code="A", batch_no=None):
		return frappe._dict(
			item_code=item_code,
			warehouse="W",
			voucher_type=voucher_type,
			actual_qty=actual,
			qty_after_transaction=after,
			serial_no=None,
			batch_no=batch_no,
		)

	def test_running_balance_matches(self):
		sles = [self.sle(5, 15), self.sle(-2, 13)]
		self.assertTrue(fix_balance_qty.has_correct_balance_qty(self.previous(10), sles))

	def test_mismatch_is_detected(self):
		sles = [self.sle(5, 15), self.sle(-2, 99)]
		self.assertFalse(fix_balance_qty.has_correct_balance_qty(self.previous(10), sles))

	def test_other_item_rows_are_ignored(self):
		sles = [self.sle(5, 999, item_code="B"), self.sle(5, 15)]
		self.assertTrue(fix_balance_qty.has_correct_balance_qty(self.previous(10), sles))

	def test_reconciliation_resets_balance_unless_serial_or_batch(self):
		reset = [self.sle(0, 3, voucher_type="Stock Reconciliation"), self.sle(1, 4)]
		self.assertTrue(fix_balance_qty.has_correct_balance_qty(self.previous(10), reset))
		batched = [self.sle(0, 3, voucher_type="Stock Reconciliation", batch_no="B1")]
		self.assertFalse(fix_balance_qty.has_correct_balance_qty(self.previous(10), batched))


class TestFixBalanceQty(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_item(properties={"is_stock_item": 1, "valuation_rate": 10}).name
		cls.old_receipt = make_stock_entry(
			item_code=cls.item,
			qty=10,
			to_warehouse=WAREHOUSE,
			rate=10,
			company=COMPANY,
			posting_date=add_days(today(), -3),
			posting_time=nowtime(),
		)
		cls.receipt = make_stock_entry(
			item_code=cls.item, qty=5, to_warehouse=WAREHOUSE, rate=10, company=COMPANY
		)
		cls.issue = make_stock_entry(item_code=cls.item, qty=2, from_warehouse=WAREHOUSE, company=COMPANY)

	def reposts_for_item(self):
		return frappe.get_all(
			"Repost Item Valuation",
			filters={"item_code": self.item, "warehouse": WAREHOUSE, "based_on": "Item and Warehouse"},
			fields=["name", "posting_date", "docstatus", "allow_negative_stock", "company"],
		)

	def test_consistent_ledger_creates_no_repost(self):
		fix_balance_qty.execute()
		self.assertEqual(self.reposts_for_item(), [])

	def test_wrong_recent_balance_is_reposted_from_previous_entry(self):
		corrupt_balance(self, sle_of(self.issue))
		existing = {repost.name for repost in self.reposts_for_item()}
		fix_balance_qty.execute()
		reposts = [repost for repost in self.reposts_for_item() if repost.name not in existing]
		self.assertEqual(len(reposts), 1)
		self.assertEqual(str(reposts[0].posting_date), add_days(today(), -3))
		self.assertEqual(
			(reposts[0].docstatus, reposts[0].allow_negative_stock, reposts[0].company), (1, 1, COMPANY)
		)
		self.assertEqual(
			frappe.db.get_value("Stock Ledger Entry", sle_of(self.issue), "qty_after_transaction"), 13
		)

	def test_create_repost_item_valuation_entry(self):
		fix_balance_qty.create_repost_item_valuation_entry(
			{
				"item_code": self.item,
				"warehouse": WAREHOUSE,
				"posting_date": today(),
				"posting_time": nowtime(),
				"company": COMPANY,
			}
		)
		reposts = self.reposts_for_item()
		self.assertEqual(len(reposts), 1)
		self.assertEqual(reposts[0].docstatus, 1)
