import base64
import hashlib
import json
from unittest.mock import MagicMock, patch

import frappe
import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from frappe.tests import IntegrationTestCase

from csf_tz.csf_tz.doctype.vehicle_fine_record import vehicle_fine_record as module
from csf_tz.csf_tz.doctype.vehicle_fine_record.vehicle_fine_record import (
	TPF_SECRET,
	check_fine_all_vehicles,
	decode_tpf_response,
	get_fine,
	is_valid_number_plate,
	normalize_number_plate,
	send_pending_vehicle_fine_notifications,
	sync_vehicle_fines,
)
from csf_tz.tests.test_vehicle_authority import (
	AUTHORITY_USER,
	configure_authority_notifications,
	make_vehicle,
)

PLATE = "T444DDD"


def encrypt_tpf_payload(data):
	key = TPF_SECRET[:32].ljust(32, "\0").encode()
	iv = hashlib.sha256(TPF_SECRET.encode()).hexdigest()[:16].encode()
	padder = padding.PKCS7(128).padder()
	padded = padder.update(json.dumps(data).encode()) + padder.finalize()
	encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
	return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def tpf_response(pending_transactions, status_code=200):
	response = MagicMock(status_code=status_code, text="ok")
	response.json.return_value = {"pending_transactions": pending_transactions}
	return response


def fine(reference, status="PENDING", charge=30000, penalty=0):
	return {
		"reference": reference,
		"status": status,
		"charge": charge,
		"penalty": penalty,
		"offence": "Speeding",
		"issued_date": "2026-01-15",
		"licence": "LIC-1",
		"location": "Dar es Salaam",
		"officer": "Officer One",
	}


class TestVehicleFinePlates(IntegrationTestCase):
	def test_normalize_number_plate(self):
		self.assertEqual(normalize_number_plate(" t-123 abc "), "T123ABC")
		self.assertEqual(normalize_number_plate("T123ABCXYZ"), "T123ABC")
		self.assertIsNone(normalize_number_plate(""))
		self.assertIsNone(normalize_number_plate("---"))

	def test_is_valid_number_plate(self):
		self.assertTrue(is_valid_number_plate("T123ABC"))
		self.assertTrue(is_valid_number_plate("TZ999AB"))
		self.assertFalse(is_valid_number_plate("T12ABC"))
		self.assertFalse(is_valid_number_plate("1234567"))
		self.assertFalse(is_valid_number_plate(None))

	def test_decode_tpf_response_passthrough_and_decrypt(self):
		plain = {"pending_transactions": [fine("REF-X")]}
		self.assertEqual(decode_tpf_response(plain), plain)

		payload = encrypt_tpf_payload(plain)
		self.assertEqual(decode_tpf_response({"payload": payload}), plain)

		double_encoded = base64.b64encode(payload.encode()).decode()
		self.assertEqual(decode_tpf_response({"payload": double_encoded}), plain)


class TestVehicleFineSync(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.vehicle = make_vehicle(PLATE)
		cls.commit_patch = patch.object(frappe.db, "commit")
		cls.commit_patch.start()
		cls.addClassCleanup(cls.commit_patch.stop)

	def test_invalid_plates_are_rejected_without_calling_tpf(self):
		with patch("requests.post") as post:
			self.assertEqual(sync_vehicle_fines("")["status"], "invalid")
			self.assertEqual(sync_vehicle_fines("T12")["status"], "invalid")
		post.assert_not_called()

	def test_sync_creates_marks_paid_and_notifies(self):
		configure_authority_notifications(
			["Vehicle Fine"], vehicle_fine_notify_on_new=1, vehicle_fine_notify_on_status_change=1
		)
		with (
			patch("requests.post", return_value=tpf_response([fine("REF-A"), {"no": "reference"}])),
			patch("frappe.sendmail") as sendmail,
		):
			result = sync_vehicle_fines("t 444 ddd")
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["fine_list"], ["REF-A"])
		record = frappe.get_doc("Vehicle Fine Record", "REF-A")
		self.assertEqual(record.vehicle, PLATE)
		self.assertEqual(record.vehicle_doc, self.vehicle.name)
		self.assertEqual(record.status, "PENDING")
		self.assertEqual(record.total, 30000)
		self.assertIsNotNone(record.authority_notified_on_new)
		self.assertEqual(record.authority_last_notified_status, "PENDING")
		self.assertEqual(sendmail.call_count, 1)

		with (
			patch("requests.post", return_value=tpf_response([fine("REF-A"), fine("REF-B")])),
			patch("frappe.sendmail") as sendmail,
		):
			self.assertEqual(get_fine(PLATE), ["REF-A", "REF-B"])
		self.assertTrue(frappe.db.exists("Vehicle Fine Record", "REF-B"))
		self.assertEqual(sendmail.call_count, 1)

		with (
			patch("requests.post", return_value=tpf_response([fine("REF-B")])),
			patch("frappe.sendmail") as sendmail,
		):
			sync_vehicle_fines(PLATE)
		self.assertEqual(frappe.db.get_value("Vehicle Fine Record", "REF-A", "status"), "PAID")
		self.assertEqual(
			frappe.db.get_value("Vehicle Fine Record", "REF-A", "authority_last_notified_status"), "PAID"
		)
		self.assertEqual(frappe.db.get_value("Vehicle Fine Record", "REF-B", "status"), "PENDING")
		sendmail.assert_called_once()

		with patch("requests.post", return_value=tpf_response([])), patch("frappe.sendmail") as sendmail:
			self.assertEqual(get_fine(PLATE), [])
		self.assertEqual(frappe.db.get_value("Vehicle Fine Record", "REF-B", "status"), "PAID")
		sendmail.assert_called_once()

	def test_sync_without_references_returns_success(self):
		with patch("requests.post", return_value=tpf_response([{"charge": 1}])):
			result = sync_vehicle_fines(PLATE)
		self.assertEqual(result["message"], "No fine references")

	def test_rate_limited_and_request_errors(self):
		with patch("requests.post", return_value=tpf_response([], status_code=429)):
			self.assertEqual(sync_vehicle_fines(PLATE)["status"], "rate_limited")

		with patch("requests.post", side_effect=requests.exceptions.ConnectionError("down")):
			result = sync_vehicle_fines(PLATE)
		self.assertEqual(result["status"], "retryable_error")
		self.assertEqual(result["message"], "down")

	def test_invalid_json_is_logged(self):
		response = MagicMock(status_code=200, text="<html>")
		response.json.side_effect = ValueError("bad json")
		with patch("requests.post", return_value=response), patch("frappe.log_error") as log_error:
			result = sync_vehicle_fines(PLATE)
		self.assertEqual(result["status"], "error")
		log_error.assert_called_once()

	def test_check_fine_all_vehicles_deduplicates_plates(self):
		records = [
			frappe._dict(plate_number="t 555 eee"),
			frappe._dict(plate_number="T555EEE"),
			frappe._dict(plate_number="BAD"),
			frappe._dict(plate_number="T666FFF"),
		]
		with (
			patch.object(module, "get_vehicle_like_records", return_value=iter(records)),
			patch.object(module, "get_fine") as get_fine_mock,
		):
			result = check_fine_all_vehicles()
		self.assertEqual(result["message"], "Processed fine checks for 2 unique vehicle-like records")
		self.assertEqual(
			[call.kwargs["number_plate"] for call in get_fine_mock.call_args_list], ["T555EEE", "T666FFF"]
		)

	def test_send_pending_vehicle_fine_notifications(self):
		configure_authority_notifications(
			["Vehicle Fine"], vehicle_fine_notify_on_new=1, vehicle_fine_notify_on_status_change=1
		)
		record = frappe.get_doc(
			{
				"doctype": "Vehicle Fine Record",
				"reference": "REF-PENDING",
				"vehicle": "T445DDD",
				"status": "PENDING",
			}
		).insert()
		frappe.db.set_value("Vehicle Fine Record", record.name, "authority_last_notified_status", "OLD")

		with patch("frappe.sendmail") as sendmail:
			send_pending_vehicle_fine_notifications()
		record.reload()
		self.assertIsNotNone(record.authority_notified_on_new)
		self.assertEqual(record.authority_last_notified_status, "PENDING")
		self.assertEqual(sendmail.call_count, 2)
		for call in sendmail.call_args_list:
			self.assertEqual(call.kwargs["recipients"], [AUTHORITY_USER])

	def test_validate_clears_vehicle_doc_for_unknown_plate(self):
		record = frappe.get_doc(
			{"doctype": "Vehicle Fine Record", "reference": "REF-UNKNOWN", "vehicle": "T777GGG"}
		).insert()
		self.assertIsNone(record.vehicle_doc)
