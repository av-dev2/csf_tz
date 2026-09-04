import json

import frappe
from erpnext.manufacturing.doctype.work_order.test_work_order import make_wo_order_test_record
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from csf_tz.csf_tz.page.jobcards.jobcards import get_employees, get_job_cards, save_doc
from csf_tz.csf_tz.page.scan_qrcode.scan_qrcode import add_biometric_log
from csf_tz.sales_and_marketing.doctype.customer_item.customer_item import CustomerItem
from csf_tz.sales_and_marketing.doctype.products_of_interest.products_of_interest import ProductsofInterest
from csf_tz.tests.integration_fixtures import COMPANY

NO_PERMISSION_USER = "csf_tz_noperm@example.com"


def make_user_without_roles():
	if not frappe.db.exists("User", NO_PERMISSION_USER):
		frappe.get_doc(
			{"doctype": "User", "email": NO_PERMISSION_USER, "first_name": "No Perm", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	frappe.db.delete("Has Role", {"parent": NO_PERMISSION_USER})
	return NO_PERMISSION_USER


class TestMealCount(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.get_doc({"doctype": "CSF TZ Biometric User Type", "user_type": "_Test Staff"}).insert()
		cls.biometric_user = frappe.get_doc(
			{
				"doctype": "CSF TZ Biometric User",
				"user_id": "BIO-001",
				"uid": "1",
				"user_name": "Bio One",
				"user_type": "_Test Staff",
				"erpnext_user": "test@example.com",
			}
		).insert()

	def test_biometric_user_is_named_by_user_id(self):
		self.assertEqual(self.biometric_user.name, "BIO-001")
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc(
				{"doctype": "CSF TZ Biometric User", "user_id": "BIO-001", "uid": "2", "user_name": "Dup"}
			).insert()

	def test_biometric_device_and_meal_type(self):
		device = frappe.get_doc(
			{"doctype": "CSF TZ Biometric Device", "device_id": "DEV-001", "device_nick_name": "Gate"}
		).insert()
		self.assertEqual(device.name, "DEV-001")

		meal = frappe.get_doc(
			{
				"doctype": "CSF TZ Meal Type",
				"meal_name": "_Test Lunch",
				"meal_type": "Lunch",
				"start_time": "12:00:00",
				"end_time": "14:00:00",
			}
		).insert()
		self.assertEqual(meal.name, "_Test Lunch")

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "CSF TZ Meal Type",
					"meal_name": "_Test Snack",
					"meal_type": "Snack",
					"start_time": "12:00:00",
					"end_time": "14:00:00",
				}
			).insert()

	def test_add_biometric_log(self):
		log = add_biometric_log("BIO-001")
		self.assertEqual(log.doctype, "CSF TZ Biometric Log")
		self.assertEqual((log.user_id, log.uid), ("BIO-001", "BIO-001"))
		self.assertIsNotNone(log.timestamp)
		self.assertTrue(frappe.db.exists("CSF TZ Biometric Log", log.name))

		frappe.get_doc(
			{
				"doctype": "CSF TZ Biometric Log",
				"biometric_user": "BIO-001",
				"user_id": "BIO-001",
				"timestamp": now_datetime(),
				"punch_direction": "IN",
			}
		).insert()
		self.assertEqual(frappe.db.count("CSF TZ Biometric Log", {"user_id": "BIO-001"}), 2)

	def test_sales_child_doctypes(self):
		self.assertTrue(frappe.get_meta("Products of Interest").istable)
		self.assertTrue(frappe.get_meta("Customer Item").istable)
		self.assertIsInstance(frappe.new_doc("Products of Interest"), ProductsofInterest)
		self.assertIsInstance(frappe.new_doc("Customer Item"), CustomerItem)


class TestJobCardsPage(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.work_order = make_wo_order_test_record(item="_Test FG Item", qty=1, company=COMPANY)
		cls.job_card = frappe.get_doc(
			{
				"doctype": "Job Card",
				"work_order": cls.work_order.name,
				"production_item": cls.work_order.production_item,
				"bom_no": cls.work_order.bom_no,
				"operation": "_Test Operation 1",
				"workstation": "_Test Workstation 1",
				"company": COMPANY,
				"for_quantity": 1,
				"wip_warehouse": cls.work_order.wip_warehouse,
			}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_get_job_cards_includes_operation_and_time_logs(self):
		cards = {card["name"]: card for card in get_job_cards()}
		self.assertIn(self.job_card.name, cards)
		card = cards[self.job_card.name]
		self.assertEqual(card["operation"].name, "_Test Operation 1")
		self.assertEqual(card["time_logs"], [])
		self.assertIn("work_order_image", card)

	def test_get_employees(self):
		employees = get_employees(COMPANY)
		self.assertIn("_T-Employee-00001", [row["name"] for row in employees])
		self.assertEqual(get_employees("No Such Company"), [])

	def test_save_doc_updates_and_submits(self):
		start = now_datetime()
		payload = {
			"name": self.job_card.name,
			"remarks": "updated from page",
			"time_logs": [
				{
					"from_time": str(start),
					"to_time": str(add_to_date(start, minutes=30)),
					"time_in_mins": 30,
					"completed_qty": 1,
				}
			],
		}
		saved = save_doc(json.dumps(payload))
		self.assertEqual(saved.remarks, "updated from page")
		self.assertEqual(saved.total_completed_qty, 1)
		self.assertEqual(saved.status, "Work In Progress")

		submitted = save_doc(json.dumps({"name": self.job_card.name}), action="Submit")
		self.assertEqual(submitted.docstatus, 1)
		self.assertNotIn(self.job_card.name, [card["name"] for card in get_job_cards()])

	def test_page_methods_require_permission(self):
		frappe.set_user(make_user_without_roles())
		with self.assertRaises(frappe.PermissionError):
			get_job_cards()
		with self.assertRaises(frappe.PermissionError):
			get_employees(COMPANY)
		with self.assertRaises(frappe.PermissionError):
			save_doc(json.dumps({"name": self.job_card.name, "remarks": "x"}))
