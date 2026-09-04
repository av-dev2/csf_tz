import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

CUSTOMER = "_Test Customer"


def make_past_serial_no(serial_no, **values):
	doc = frappe.get_doc(
		{
			"doctype": "Past Serial No",
			"serial_no": serial_no,
			"item_code": "_Test Item",
			"customer": CUSTOMER,
			"amount": 1500,
			"date_of_sale": today(),
			**values,
		}
	)
	return doc.insert()


class TestPastSerialNo(IntegrationTestCase):
	def test_serial_no_is_the_document_name(self):
		doc = make_past_serial_no("PSN-0001", payment_plan=[{"planned_date": today(), "planned_amount": 500}])
		self.assertEqual(doc.name, "PSN-0001")
		self.assertEqual(doc.payment_plan[0].parent, "PSN-0001")
		self.assertEqual(frappe.db.get_value("Past Serial No", "PSN-0001", "customer"), CUSTOMER)

	def test_serial_no_is_mandatory(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Past Serial No", "item_code": "_Test Item"}).insert()

	def test_duplicate_serial_no_is_rejected(self):
		make_past_serial_no("PSN-0002")
		with self.assertRaises(frappe.DuplicateEntryError):
			make_past_serial_no("PSN-0002")

	def test_unknown_customer_is_rejected(self):
		with self.assertRaises(frappe.LinkValidationError):
			make_past_serial_no("PSN-0003", customer="No Such Customer")

	def test_submit_and_cancel(self):
		doc = make_past_serial_no("PSN-0004")
		doc.submit()
		self.assertEqual(doc.docstatus, 1)
		doc.cancel()
		self.assertEqual(frappe.db.get_value("Past Serial No", doc.name, "docstatus"), 2)


class TestPastSales(IntegrationTestCase):
	def test_past_sale_links_to_past_serial_no(self):
		serial = make_past_serial_no("PSN-0010")
		sale = frappe.get_doc(
			{
				"doctype": "Past Sales",
				"naming_series": "PS-",
				"item_sold": "_Test Item",
				"customer": CUSTOMER,
				"amount": 1500,
				"sold_date": today(),
				"serial_no": serial.name,
				"plate_no": "T 123 ABC",
			}
		).insert()
		self.assertTrue(sale.name.startswith("PS-"))
		self.assertEqual(frappe.db.get_value("Past Sales", sale.name, "serial_no"), "PSN-0010")

	def test_unknown_serial_no_is_rejected(self):
		with self.assertRaises(frappe.LinkValidationError):
			frappe.get_doc({"doctype": "Past Sales", "naming_series": "PS-", "serial_no": "missing"}).insert()


class TestCommunications(IntegrationTestCase):
	def test_communication_is_numbered_by_series(self):
		doc = frappe.get_doc(
			{
				"doctype": "Communications",
				"naming_series": "COMM-",
				"type_of_communication": "Phone Call",
				"customer": CUSTOMER,
				"contacted_by": "Administrator",
				"date_of_communication": frappe.utils.now(),
				"communication_feedback": "Customer asked for a quotation",
			}
		).insert()
		self.assertTrue(doc.name.startswith("COMM-"))
		self.assertEqual(frappe.db.get_value("Communications", doc.name, "customer"), CUSTOMER)

	def test_invalid_communication_type_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Communications", "naming_series": "COMM-", "type_of_communication": "Fax"}
			).insert()

	def test_unknown_user_is_rejected(self):
		with self.assertRaises(frappe.LinkValidationError):
			frappe.get_doc(
				{"doctype": "Communications", "naming_series": "COMM-", "contacted_by": "nobody@example.com"}
			).insert()


class TestMarketingDept(IntegrationTestCase):
	def test_insert(self):
		doc = frappe.get_doc({"doctype": "Marketing Dept", "dept_name": "Digital"}).insert()
		self.assertEqual(frappe.db.get_value("Marketing Dept", doc.name, "dept_name"), "Digital")


class TestAllertCustom(IntegrationTestCase):
	def test_insert(self):
		doc = frappe.get_doc({"doctype": "Allert Custom"}).insert()
		self.assertTrue(frappe.db.exists("Allert Custom", doc.name))
