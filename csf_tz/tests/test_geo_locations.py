import frappe
from frappe.tests import IntegrationTestCase

from csf_tz.csf_tz.doctype.tz_district.tz_district import TZDistrict
from csf_tz.csf_tz.doctype.tz_region.tz_region import TZRegion
from csf_tz.csf_tz.doctype.tz_village.tz_village import TZVillage
from csf_tz.csf_tz.doctype.tz_ward.tz_ward import TZWard

REGION = "_Test Geo Region"


def make_region(region=REGION):
	if frappe.db.exists("TZ Region", region):
		return frappe.get_doc("TZ Region", region)
	return frappe.get_doc({"doctype": "TZ Region", "region": region}).insert()


def make_district(district, region=REGION):
	name = frappe.db.get_value("TZ District", {"district": district})
	if name:
		return frappe.get_doc("TZ District", name)
	return frappe.get_doc({"doctype": "TZ District", "district": district, "region": region}).insert()


class TestGeoLocations(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.region = make_region()
		cls.district = make_district("_Test Geo District")

	def test_region_is_named_by_region_field(self):
		self.assertIsInstance(self.region, TZRegion)
		self.assertEqual(self.region.name, REGION)

	def test_region_must_be_unique(self):
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc({"doctype": "TZ Region", "region": REGION}).insert()

	def test_district_uses_expression_naming_and_links_region(self):
		self.assertIsInstance(self.district, TZDistrict)
		self.assertTrue(self.district.name.startswith("D-"))
		self.assertEqual(self.district.region, REGION)

	def test_district_requires_region(self):
		with self.assertRaises(frappe.MandatoryError):
			frappe.get_doc({"doctype": "TZ District", "district": "_Test Geo Orphan District"}).insert()

	def test_district_rejects_unknown_region(self):
		with self.assertRaises(frappe.LinkValidationError):
			frappe.get_doc(
				{"doctype": "TZ District", "district": "_Test Geo Bad District", "region": "No Such Region"}
			).insert()

	def test_ward_and_village_chain(self):
		ward = frappe.get_doc(
			{"doctype": "TZ Ward", "ward": "_Test Geo Ward", "district": self.district.name}
		).insert()
		self.assertIsInstance(ward, TZWard)
		self.assertTrue(ward.name.startswith("W-"))

		village = frappe.get_doc(
			{"doctype": "TZ Village", "village": "_Test Geo Village", "ward": ward.name, "postcode": "11101"}
		).insert()
		self.assertIsInstance(village, TZVillage)
		self.assertTrue(village.name.startswith("V-"))
		self.assertEqual(frappe.db.get_value("TZ Village", village.name, "postcode"), "11101")

	def test_village_requires_ward(self):
		with self.assertRaises(frappe.MandatoryError):
			frappe.get_doc({"doctype": "TZ Village", "village": "_Test Geo Orphan Village"}).insert()

	def test_region_cannot_be_deleted_while_linked(self):
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("TZ Region", REGION)
