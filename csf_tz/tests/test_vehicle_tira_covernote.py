import json
from unittest.mock import patch

import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from csf_tz.csf_tz.doctype.tz_insurance_cover_note import tz_insurance_cover_note as module
from csf_tz.csf_tz.doctype.tz_insurance_cover_note.tz_insurance_cover_note import (
	fetch_and_update_covernote,
	get_covernote_details,
	notify_tira_covernote_expiry,
	update_covernote_docs,
)
from csf_tz.tests.integration_fixtures import FakeResponse
from csf_tz.tests.test_vehicle_authority import AUTHORITY_USER, configure_authority_notifications

PLATE = "T444TTT"
JAN_2024 = 1704067200000


def tira_record(cover_note_number="CN-001", status="Active"):
	return {
		"coverNoteNumber": cover_note_number,
		"coverNoteStartDate": JAN_2024,
		"coverNoteEndDate": 1735603200000,
		"statusTitle": status,
		"currencyCode": "TZS",
		"totalPremiumAmountIncludingTax": 150000,
		"isMotor": True,
		"motor": {
			"registrationNumber": PLATE,
			"make": "Toyota",
			"createdDate": JAN_2024,
			"updatedDate": None,
		},
		"company": {
			"companyName": "Insurer Ltd",
			"incorporationDate": JAN_2024,
			"shareholders": [{"name": "Owner"}],
		},
		"policyHolders": [{"policyHolderFullName": "John Doe", "policyHolderBirthDate": 631152000000}],
	}


class TestGetCovernoteDetails(IntegrationTestCase):
	def test_success_returns_json(self):
		with patch("requests.post", return_value=FakeResponse(200, {"data": []})) as post:
			self.assertEqual(get_covernote_details(PLATE), {"data": []})
		self.assertEqual(json.loads(post.call_args.kwargs["data"]), {"paramType": 2, "searchParam": PLATE})

	def test_client_error_is_logged_without_retry(self):
		with (
			patch("requests.post", return_value=FakeResponse(404, {}, text="missing")) as post,
			patch("frappe.log_error") as log_error,
		):
			self.assertIsNone(get_covernote_details(PLATE))
		self.assertEqual(post.call_count, 1)
		self.assertEqual(log_error.call_args.kwargs["title"], "Tiramis API Error")

	def test_server_errors_and_timeouts_are_retried(self):
		with (
			patch("requests.post", return_value=FakeResponse(500, {})) as post,
			patch.object(module, "sleep"),
		):
			self.assertIsNone(get_covernote_details(PLATE))
		self.assertEqual(post.call_count, 3)

		with (
			patch("requests.post", side_effect=requests.exceptions.Timeout()) as post,
			patch.object(module, "sleep") as sleep,
		):
			self.assertIsNone(get_covernote_details(PLATE))
		self.assertEqual(post.call_count, 3)
		self.assertEqual(sleep.call_count, 2)

		with (
			patch("requests.post", side_effect=requests.exceptions.InvalidURL()),
			patch("frappe.log_error") as log,
		):
			self.assertIsNone(get_covernote_details(PLATE))
		log.assert_called_once()

	def test_invalid_json_is_logged(self):
		with (
			patch("requests.post", return_value=FakeResponse(200, None, text="<html>")),
			patch("frappe.log_error") as log_error,
		):
			self.assertIsNone(get_covernote_details(PLATE))
		self.assertEqual(log_error.call_args.kwargs["title"], "Tiramis API: Invalid JSON")


class TestCovernoteSync(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.commit_patch = patch.object(frappe.db, "commit")
		cls.commit_patch.start()
		cls.addClassCleanup(cls.commit_patch.stop)

	def test_fetch_and_update_covernote_creates_and_updates(self):
		with patch.object(module, "get_covernote_details", return_value={"data": [tira_record()]}):
			fetch_and_update_covernote(PLATE)

		doc = frappe.get_doc("TZ Insurance Cover Note", "CN-001")
		self.assertEqual(doc.vehicle, PLATE)
		self.assertEqual(doc.statustitle, "Active")
		self.assertEqual(doc.covernotestartdate, "2024-01-01 00:00:00")
		self.assertEqual(doc.covernoteenddate, "2024-12-31 00:00:00")
		self.assertEqual(doc.totalpremiumamountincludingtax, 150000)
		self.assertEqual(doc.ismotor, 1)
		self.assertEqual(len(doc.insurance_motors), 1)
		self.assertEqual(doc.insurance_motors[0].registrationnumber, PLATE)
		self.assertEqual(doc.insurance_motors[0].createddate, "2024-01-01 00:00:00")
		self.assertIsNone(doc.insurance_motors[0].updateddate)
		self.assertEqual(doc.insurance_provider[0].companyname, "Insurer Ltd")
		self.assertEqual(doc.insurance_provider[0].incorporationdate, "2024-01-01 00:00:00")
		self.assertEqual(json.loads(doc.insurance_provider[0].shareholders), [{"name": "Owner"}])
		self.assertEqual(doc.policy_holders[0].policyholderfullname, "John Doe")
		self.assertEqual(doc.policy_holders[0].policyholderbirthdate, "1990-01-01 00:00:00")

		with patch.object(
			module, "get_covernote_details", return_value={"data": [tira_record(status="Expired")]}
		):
			fetch_and_update_covernote(PLATE)
		doc.reload()
		self.assertEqual(doc.statustitle, "Expired")
		self.assertEqual(len(doc.insurance_motors), 1)
		self.assertEqual(len(doc.policy_holders), 1)

	def test_fetch_and_update_covernote_ignores_empty_and_logs_bad_records(self):
		with patch.object(module, "get_covernote_details", return_value=None):
			fetch_and_update_covernote(PLATE)
		with patch.object(module, "get_covernote_details", return_value={"data": []}):
			fetch_and_update_covernote(PLATE)
		with (
			patch.object(module, "get_covernote_details", return_value={"data": [{"statusTitle": "x"}]}),
			patch("frappe.log_error") as log_error,
		):
			fetch_and_update_covernote(PLATE)
		log_error.assert_called_once()

	def test_update_covernote_docs_deduplicates_plates(self):
		records = [
			frappe._dict(plate_number="t 444 ttt"),
			frappe._dict(plate_number="T444TTT"),
			frappe._dict(plate_number="BAD"),
			frappe._dict(plate_number="T555TTT"),
		]
		with (
			patch.object(module, "get_vehicle_like_records", return_value=iter(records)),
			patch.object(module, "fetch_and_update_covernote") as fetch,
		):
			result = update_covernote_docs()
		self.assertEqual(result["message"], "Processed covernote updates for 2 vehicles")
		self.assertEqual([call.args[0] for call in fetch.call_args_list], ["T444TTT", "T555TTT"])

	def test_notify_tira_covernote_expiry(self):
		configure_authority_notifications(["TIRA"], tira_notify_before_days=7)
		frappe.db.delete("TZ Insurance Cover Note")
		for number, days in (("CN-EXPIRED", -1), ("CN-SOON", 3), ("CN-FAR", 30)):
			frappe.get_doc(
				{
					"doctype": "TZ Insurance Cover Note",
					"covernotenumber": number,
					"vehicle": PLATE,
					"covernoteenddate": f"{add_days(nowdate(), days)} 00:00:00",
				}
			).insert()
		frappe.get_doc({"doctype": "TZ Insurance Cover Note", "covernotenumber": "CN-NODATE"}).insert()

		with patch("frappe.sendmail") as sendmail:
			notify_tira_covernote_expiry()
		self.assertEqual(sendmail.call_count, 2)
		subjects = sorted(call.kwargs["subject"] for call in sendmail.call_args_list)
		self.assertTrue(subjects[0].startswith("TIRA Cover Note Expired"))
		self.assertTrue(subjects[1].startswith("TIRA Cover Note Expiry Reminder"))
		self.assertEqual(sendmail.call_args.kwargs["recipients"], [AUTHORITY_USER])
		key = frappe.db.get_value(
			"TZ Insurance Cover Note", "CN-SOON", "authority_last_expiry_notification_key"
		)
		self.assertTrue(key.startswith("pre-expiry:"))

		with patch("frappe.sendmail") as sendmail:
			notify_tira_covernote_expiry()
		sendmail.assert_not_called()
