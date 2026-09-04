from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from csf_tz import vehicle_authority
from csf_tz.vehicle_authority import (
	get_authority_notification_recipients,
	get_authority_notification_roles,
	get_unique_vehicle_plates,
	get_vehicle_docname_by_plate,
	get_vehicle_like_doctypes,
	get_vehicle_like_records,
	get_vehicle_plate,
	has_vehicle_plate_field,
	is_authority_notification_enabled,
	is_authority_notification_event_enabled,
	run_daily_authority_notifications,
	send_authority_notification,
)

AUTHORITY_ROLE = "_Test Authority Notification Role"
AUTHORITY_USER = "test@example.com"


def make_vehicle(plate):
	if frappe.db.exists("Vehicle", plate):
		return frappe.get_doc("Vehicle", plate)
	return frappe.get_doc(
		{
			"doctype": "Vehicle",
			"license_plate": plate,
			"make": "Toyota",
			"model": "Hilux",
			"last_odometer": 100,
			"fuel_type": "Diesel",
			"uom": "Nos",
		}
	).insert()


def make_authority_role():
	if not frappe.db.exists("Role", AUTHORITY_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": AUTHORITY_ROLE}).insert()
	frappe.get_doc("User", AUTHORITY_USER).add_roles(AUTHORITY_ROLE)


def configure_authority_notifications(reference_types=(), **flags):
	"""Enable notifications for the given reference types and route them to AUTHORITY_ROLE."""
	make_authority_role()
	settings = frappe.get_single("CSF TZ Settings")
	settings.authority_notification_roles = []
	for reference_type in reference_types:
		settings.append(
			"authority_notification_roles", {"reference_type": reference_type, "role": AUTHORITY_ROLE}
		)
	for fieldname in vehicle_authority.AUTHORITY_ENABLE_FIELD_MAP.values():
		settings.set(fieldname, 0)
	for fieldname in vehicle_authority.AUTHORITY_EVENT_ENABLE_FIELD_MAP.values():
		settings.set(fieldname, 0)
	for reference_type in reference_types:
		settings.set(vehicle_authority.AUTHORITY_ENABLE_FIELD_MAP[reference_type], 1)
	for fieldname, value in flags.items():
		settings.set(fieldname, value)
	settings.save(ignore_permissions=True)
	return settings


class TestVehicleAuthority(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.vehicle = make_vehicle("T111AAA")

	def test_get_vehicle_plate_from_document_and_values(self):
		self.assertEqual(get_vehicle_plate(self.vehicle), "T111AAA")
		meta = frappe.get_meta("Vehicle")
		self.assertEqual(get_vehicle_plate(frappe._dict(license_plate=" T222BBB "), meta=meta), "T222BBB")
		self.assertIsNone(get_vehicle_plate(frappe._dict(license_plate=""), meta=meta))

	def test_has_vehicle_plate_field(self):
		self.assertTrue(has_vehicle_plate_field(frappe.get_meta("Vehicle")))
		self.assertFalse(has_vehicle_plate_field(frappe.get_meta("Item")))

	def test_vehicle_like_doctypes_and_records(self):
		doctypes = [doctype for doctype, _meta in get_vehicle_like_doctypes()]
		self.assertIn("Vehicle", doctypes)

		records = [record for record in get_vehicle_like_records() if record.doctype == "Vehicle"]
		self.assertIn(
			("Vehicle", "T111AAA", "T111AAA"), [(r.doctype, r.name, r.plate_number) for r in records]
		)

	def test_get_vehicle_docname_by_plate(self):
		self.assertEqual(get_vehicle_docname_by_plate("T111AAA"), self.vehicle.name)
		self.assertIsNone(get_vehicle_docname_by_plate("T999ZZZ"))
		self.assertIsNone(get_vehicle_docname_by_plate(None))

	def test_get_unique_vehicle_plates(self):
		make_vehicle("t 333 ccc")
		plates = get_unique_vehicle_plates(
			normalize_number_plate=lambda plate: plate.replace(" ", "").upper(),
			is_valid_number_plate=lambda plate: plate != "T111AAA",
		)
		self.assertIn("T333CCC", plates)
		self.assertNotIn("T111AAA", plates)
		self.assertIn("T111AAA", get_unique_vehicle_plates())

	def test_notification_roles_and_recipients(self):
		configure_authority_notifications(["Vehicle Fine"])
		self.assertEqual(get_authority_notification_roles("Vehicle Fine"), [AUTHORITY_ROLE])
		self.assertEqual(get_authority_notification_roles("LATRA License"), [])
		self.assertEqual(get_authority_notification_roles("Unknown"), [])
		self.assertEqual(get_authority_notification_recipients("Vehicle Fine"), [AUTHORITY_USER])
		self.assertEqual(get_authority_notification_recipients("TIRA"), [])

	def test_disabled_users_are_not_recipients(self):
		configure_authority_notifications(["TIRA"])
		frappe.db.set_value("User", AUTHORITY_USER, "enabled", 0)
		try:
			self.assertEqual(get_authority_notification_recipients("TIRA"), [])
		finally:
			frappe.db.set_value("User", AUTHORITY_USER, "enabled", 1)

	def test_enabled_flags(self):
		configure_authority_notifications(["LATRA Offence"], latra_offence_notify_on_new=1)
		self.assertTrue(is_authority_notification_enabled("LATRA Offence"))
		self.assertFalse(is_authority_notification_enabled("TIRA"))
		self.assertFalse(is_authority_notification_enabled("Unknown"))
		self.assertTrue(is_authority_notification_event_enabled("LATRA Offence", "new"))
		self.assertFalse(is_authority_notification_event_enabled("LATRA Offence", "status_change"))
		self.assertTrue(is_authority_notification_event_enabled("LATRA Offence", "unknown_event"))

	def test_send_authority_notification(self):
		configure_authority_notifications([])
		self.assertEqual(send_authority_notification("TIRA", "s", "m"), {"sent": False, "reason": "disabled"})

		settings = configure_authority_notifications(["TIRA"])
		settings.authority_notification_roles = []
		settings.save(ignore_permissions=True)
		self.assertEqual(
			send_authority_notification("TIRA", "s", "m"), {"sent": False, "reason": "no_recipients"}
		)

		configure_authority_notifications(["TIRA"])
		with patch("frappe.sendmail") as sendmail:
			result = send_authority_notification("TIRA", "Subject", "Message")
		self.assertTrue(result["sent"])
		self.assertEqual(result["recipients"], [AUTHORITY_USER])
		sendmail.assert_called_once_with(
			recipients=[AUTHORITY_USER], subject="Subject", message="Message", now=True
		)

	def test_run_daily_authority_notifications_calls_every_notifier(self):
		targets = [
			"csf_tz.csf_tz.doctype.latra_licenses.latra_licenses.notify_latra_license_expiry",
			"csf_tz.csf_tz.doctype.latra_licenses.latra_licenses.send_pending_latra_offence_notifications",
			"csf_tz.csf_tz.doctype.tz_insurance_cover_note.tz_insurance_cover_note.notify_tira_covernote_expiry",
			"csf_tz.csf_tz.doctype.vehicle_fine_record.vehicle_fine_record.send_pending_vehicle_fine_notifications",
		]
		patchers = [patch(target) for target in targets]
		mocks = [patcher.start() for patcher in patchers]
		try:
			run_daily_authority_notifications()
		finally:
			for patcher in patchers:
				patcher.stop()
		for mock in mocks:
			mock.assert_called_once_with()

	def test_run_daily_authority_notifications_end_to_end_with_nothing_enabled(self):
		configure_authority_notifications([])
		with patch.object(frappe.db, "commit"), patch("frappe.sendmail") as sendmail:
			run_daily_authority_notifications()
		sendmail.assert_not_called()
