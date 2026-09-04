from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from csf_tz.csftz_hooks.material_request import (
	_auto_close_material_request_batch,
	auto_close_material_request,
)

COMPANY = "_Test Company"
MODULE = "csf_tz.csftz_hooks.material_request"


def run_enqueued_jobs_inline(method, **kwargs):
	return method(**kwargs["kwargs"])


def make_request(days_ago):
	date = add_days(today(), -days_ago)
	request = frappe.new_doc("Material Request")
	request.update({"material_request_type": "Purchase", "company": COMPANY, "transaction_date": date})
	request.append(
		"items",
		{"item_code": "_Test Item", "qty": 10, "schedule_date": date, "warehouse": "_Test Warehouse - _TC"},
	)
	request.insert()
	return request.submit()


class TestAutoCloseMaterialRequest(IntegrationTestCase):
	def enable_auto_close(self, days):
		frappe.db.set_value(
			"Company",
			COMPANY,
			{"enable_auto_close_material_request": 1, "close_material_request_after": days},
		)
		self.addCleanup(
			frappe.db.set_value,
			"Company",
			COMPANY,
			{"enable_auto_close_material_request": 0, "close_material_request_after": 0},
		)

	def test_old_requests_are_stopped(self):
		self.enable_auto_close(7)
		old_request = make_request(10)
		with patch(f"{MODULE}.enqueue", side_effect=run_enqueued_jobs_inline):
			auto_close_material_request()
		self.assertEqual(frappe.db.get_value("Material Request", old_request.name, "status"), "Stopped")

	def test_recent_requests_are_left_open(self):
		self.enable_auto_close(7)
		recent_request = make_request(2)
		with patch(f"{MODULE}.enqueue", side_effect=run_enqueued_jobs_inline):
			auto_close_material_request()
		self.assertEqual(frappe.db.get_value("Material Request", recent_request.name, "status"), "Pending")

	def test_jobs_go_to_the_long_queue(self):
		self.enable_auto_close(7)
		old_request = make_request(10)
		with patch(f"{MODULE}.enqueue") as enqueue:
			auto_close_material_request()
		enqueue.assert_called_once()
		self.assertIs(enqueue.call_args.args[0], _auto_close_material_request_batch)
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertIn(old_request.name, enqueue.call_args.kwargs["kwargs"]["material_request_names"])

	def test_disabled_company_is_skipped(self):
		make_request(10)
		with patch(f"{MODULE}.enqueue") as enqueue:
			auto_close_material_request()
		enqueue.assert_not_called()

	def test_batch_stops_requests(self):
		request = make_request(0)
		_auto_close_material_request_batch([request.name])
		self.assertEqual(frappe.db.get_value("Material Request", request.name, "status"), "Stopped")

	def test_batch_logs_missing_request_and_continues(self):
		request = make_request(0)
		_auto_close_material_request_batch(["MR-DOES-NOT-EXIST", request.name])
		self.assertEqual(frappe.db.get_value("Material Request", request.name, "status"), "Stopped")
		self.assertTrue(
			frappe.db.exists("Error Log", {"method": "Auto Close Material Request Error: MR-DOES-NOT-EXIST"})
		)
