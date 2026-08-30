from datetime import datetime
from unittest.mock import patch

import frappe
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from csf_tz.csftz_hooks import item_reposting

MODULE = "csf_tz.csftz_hooks.item_reposting"


class TestItemReposting(IntegrationTestCase):
	def set_start_date(self, value):
		frappe.db.set_single_value("CSF TZ Settings", "sle_gle_reposting_start_date", value)

	def test_enqueue_requires_start_date(self):
		self.set_start_date(None)
		with self.assertRaises(frappe.ValidationError):
			item_reposting.enqueue_reposting_sle_gle()

	def test_enqueue_starts_long_job(self):
		self.set_start_date(add_days(today(), -1))
		with patch(f"{MODULE}.enqueue") as enqueue:
			item_reposting.enqueue_reposting_sle_gle()
		enqueue.assert_called_once()
		self.assertIs(enqueue.call_args.kwargs["method"], item_reposting.execute)
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")

	def test_start_date_is_read_as_date(self):
		self.set_start_date("2026-08-20")
		self.assertEqual(str(item_reposting.get_reposting_start_date()), "2026-08-20")

	def test_enqueue_is_whitelisted(self):
		frappe.is_whitelisted(item_reposting.enqueue_reposting_sle_gle)

	def test_execute_requires_start_date(self):
		self.set_start_date(None)
		with self.assertRaises(frappe.ValidationError):
			item_reposting.execute()

	def test_execute_skips_when_start_date_is_today(self):
		self.set_start_date(today())
		with patch(f"{MODULE}.update_entries_after") as update_entries_after:
			item_reposting.execute()
		update_entries_after.assert_not_called()

	def test_execute_reposts_entries_created_after_start_date(self):
		self.set_start_date(add_days(today(), -1))
		entry = make_stock_entry(item_code="_Test Item", qty=1, to_warehouse="_Test Warehouse - _TC", rate=10)
		sle = frappe.db.get_value(
			"Stock Ledger Entry", {"voucher_no": entry.name}, ["name", "item_code", "warehouse"], as_dict=True
		)
		with (
			patch(f"{MODULE}.update_entries_after") as update_entries_after,
			patch(f"{MODULE}.update_gl_entries_after") as update_gl_entries_after,
		):
			item_reposting.execute()
		reposted = [call.args[0] for call in update_entries_after.call_args_list]
		mine = next(args for args in reposted if args["sle_id"] == sle.name)
		self.assertEqual(
			(mine["item_code"], mine["warehouse"], mine["voucher_no"]),
			(sle.item_code, sle.warehouse, entry.name),
		)
		self.assertTrue(
			all(call.kwargs["allow_negative_stock"] for call in update_entries_after.call_args_list)
		)
		perpetual_companies = {call.kwargs["company"] for call in update_gl_entries_after.call_args_list}
		self.assertNotIn("_Test Company", perpetual_companies)
		self.assertEqual(frappe.db.auto_commit_on_many_writes, 0)

	def test_get_creation_time(self):
		self.assertIsInstance(item_reposting.get_creation_time(), datetime)
