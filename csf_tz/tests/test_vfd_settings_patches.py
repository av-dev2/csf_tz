from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from csf_tz.csf_tz.doctype.csf_api_response_log.csf_api_response_log import add_log
from csf_tz.patches import migrate_vfd_providers_to_csf_tz
from csf_tz.patches.custom_fields import vfd_providers_updated_custom_fields

VFD_FIELDS = [
	("Customer", "vfd_cust_id"),
	("Customer", "vfd_cust_id_type"),
	("Sales Invoice", "vfd_rctvnum"),
	("Sales Invoice", "vfd_status"),
	("Sales Invoice", "is_auto_generate_vfd"),
	("Item Tax Template", "vfd_taxcode"),
	("Mode of Payment", "vfd_pmttype"),
]


def custom_field_count():
	return frappe.db.count("Custom Field", {"fieldname": ["like", "vfd_%"]})


class TestVFDPatches(IntegrationTestCase):
	def test_migrate_vfd_providers_sets_app_name(self):
		for module in migrate_vfd_providers_to_csf_tz.MODULES:
			frappe.db.set_value("Module Def", module, "app_name", "erpnext", update_modified=False)
		migrate_vfd_providers_to_csf_tz.execute()
		migrate_vfd_providers_to_csf_tz.execute()
		for module in migrate_vfd_providers_to_csf_tz.MODULES:
			self.assertEqual(frappe.db.get_value("Module Def", module, "app_name"), "csf_tz")

	def test_vfd_custom_fields_patch_is_idempotent(self):
		vfd_providers_updated_custom_fields.execute()
		count = custom_field_count()
		vfd_providers_updated_custom_fields.execute()
		self.assertEqual(custom_field_count(), count)
		for doctype, fieldname in VFD_FIELDS:
			self.assertTrue(
				frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}), fieldname
			)
		options = frappe.db.get_value(
			"Custom Field", {"dt": "Sales Invoice", "fieldname": "vfd_status"}, "options"
		)
		self.assertEqual(options.split("\n"), ["Not Sent", "Pending", "Failed", "Success"])


class TestCSFAPIResponseLog(IntegrationTestCase):
	def test_add_log_stores_request_and_response(self):
		with patch.object(frappe.db, "commit"):
			name = add_log("POST", "https://api.test/x", {"h": 1}, {"b": 2}, {"r": 3}, 201)
		log = frappe.get_doc("CSF API Response Log", name)
		self.assertEqual(log.request_type, "POST")
		self.assertEqual(log.request_url, "https://api.test/x")
		self.assertEqual(log.request_header, "{'h': 1}")
		self.assertEqual(log.request_body, "{'b': 2}")
		self.assertEqual(log.response_data, "{'r': 3}")
		self.assertEqual(log.status_code, "201")
		self.assertEqual(log.user_id, "Administrator")


class TestCSFTZSettings(IntegrationTestCase):
	def settings(self):
		return frappe.get_single("CSF TZ Settings")

	def reset_populate_flag(self):
		frappe.db.set_single_value("CSF TZ Settings", "populate_tz_regions", 0)

	def realtime_message(self, publish):
		return next(
			c.kwargs["message"] for c in publish.call_args_list if c.kwargs.get("event") == "msgprint"
		)

	def test_working_days_must_be_in_range(self):
		for days in (31, 0):
			settings = self.settings()
			settings.enable_fixed_working_days_per_month = 1
			settings.working_days_per_month = days
			self.assertRaisesRegex(frappe.ValidationError, "between 1 and 30", settings.save)
		settings = self.settings()
		settings.enable_fixed_working_days_per_month = 1
		settings.working_days_per_month = 26
		settings.save()

	def test_email_queue_batch_size_updates_site_config(self):
		settings = self.settings()
		settings.override_email_queue_batch_size = 1
		settings.email_qatch_batch_size = 75
		with patch("csf_tz.csf_tz.doctype.csf_tz_settings.csf_tz_settings.update_site_config") as update:
			settings.save()
		update.assert_called_once_with("email_queue_batch_size", 75)

	def test_populate_tz_regions_enqueues_background_job(self):
		self.addCleanup(self.reset_populate_flag)
		settings = self.settings()
		settings.populate_tz_regions = 1
		with patch.object(frappe, "enqueue") as enqueue:
			settings.save()
		self.assertEqual(enqueue.call_args.kwargs["method"].__name__, "populate_tz_regions_background")
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertEqual(enqueue.call_args.kwargs["job_name"], "populate_tz_regions")
		with patch.object(frappe, "enqueue") as enqueue:
			settings.save()
		enqueue.assert_not_called()

	def test_populate_tz_regions_resets_flag_when_enqueue_fails(self):
		self.reset_populate_flag()
		settings = self.settings()
		settings.populate_tz_regions = 1
		with patch.object(frappe, "enqueue", side_effect=RuntimeError("no worker")):
			settings.save()
		self.assertEqual(frappe.db.get_single_value("CSF TZ Settings", "populate_tz_regions"), 0)

	def test_populate_tz_regions_background(self):
		settings = self.settings()
		with (
			patch("csf_tz.patches.tz_post_code.create_tz_post_code.execute") as execute,
			patch.object(frappe.db, "commit"),
			patch.object(frappe, "publish_realtime") as publish,
		):
			settings.populate_tz_regions_background()
		execute.assert_called_once()
		self.assertIn("completed", self.realtime_message(publish))
		self.assertEqual(frappe.db.get_single_value("CSF TZ Settings", "tz_regions_populated"), 1)
		self.assertEqual(frappe.db.get_single_value("CSF TZ Settings", "populate_tz_regions"), 0)

	def test_populate_tz_regions_background_failure(self):
		settings = self.settings()
		with (
			patch(
				"csf_tz.patches.tz_post_code.create_tz_post_code.execute", side_effect=RuntimeError("boom")
			),
			patch.object(frappe.db, "commit"),
			patch.object(frappe, "publish_realtime") as publish,
		):
			settings.populate_tz_regions_background()
		self.assertIn("failed: boom", self.realtime_message(publish))
		self.assertEqual(frappe.db.get_single_value("CSF TZ Settings", "tz_regions_populated"), 0)
