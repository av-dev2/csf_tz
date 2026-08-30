from unittest.mock import patch

import frappe
from erpnext.stock.utils import get_or_make_bin
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

ITEM = "_Test Item"
WAREHOUSE = "_Test Warehouse - _TC"


class TestOrderTrack(IntegrationTestCase):
	def make_order_track(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Order Track",
				"supplier": "_Test Supplier",
				"supplier_type": "International Supplier",
				"expected_arrival_date": add_days(today(), 30),
				"bl_number": "BL-001",
				**values,
			}
		).insert()

	def test_name_follows_series(self):
		doc = self.make_order_track()
		self.assertRegex(doc.name, r"^ORDERTRACK\d{4}$")
		self.assertEqual(frappe.db.get_value("Order Track", doc.name, "bl_number"), "BL-001")

	def test_submit_cancel_and_amend(self):
		doc = self.make_order_track()
		doc.submit()
		doc.cancel()
		amended = frappe.copy_doc(doc)
		amended.docstatus = 0
		amended.amended_from = doc.name
		amended.insert()
		self.assertEqual(amended.name, f"{doc.name}-1")

	def test_invalid_supplier_type_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_order_track(supplier_type="Unknown Supplier")

	def test_unknown_supplier_is_rejected(self):
		with self.assertRaises(frappe.LinkValidationError):
			self.make_order_track(supplier="No Such Supplier")

	def test_container_table_is_a_child_doctype(self):
		meta = frappe.get_meta("Order Tracking Container")
		self.assertTrue(meta.istable)
		self.assertEqual([field.fieldname for field in meta.fields], ["co_name", "co_number"])


class TestPurchaseAndStockManagementTest(IntegrationTestCase):
	def test_insert(self):
		doc = frappe.get_doc({"doctype": "Purchase And Stock Management Test", "pstest": "probe"}).insert()
		self.assertEqual(
			frappe.db.get_value("Purchase And Stock Management Test", doc.name, "pstest"), "probe"
		)


class TestItemNumber(IntegrationTestCase):
	def test_single_value_is_saved(self):
		doc = frappe.get_single("Item Number")
		doc.id = 42
		doc.save()
		self.assertEqual(frappe.db.get_single_value("Item Number", "id"), 42)


def patch_bin_loading(bin_doc=None):
	"""Serve `bin_doc` for Bin lookups and fail on any other Bin load; everything else stays real."""
	real_get_doc = frappe.get_doc

	def get_doc(*args, **kwargs):
		if args and args[0] == "Bin":
			if bin_doc and args[1] == bin_doc.name:
				return bin_doc
			raise AssertionError(f"unexpected Bin load: {args}")
		return real_get_doc(*args, **kwargs)

	return patch.object(frappe, "get_doc", side_effect=get_doc)


class TestBinSetup(IntegrationTestCase):
	def make_setup(self, **row):
		setup = frappe.get_single("Bin Setup")
		setup.bin_table = []
		setup.append("bin_table", {"item_code": ITEM, "warehouse": WAREHOUSE, **row})
		return setup

	def test_new_label_is_written_to_the_bin(self):
		bin_doc = frappe.get_doc("Bin", get_or_make_bin(ITEM, WAREHOUSE))
		setup = self.make_setup(new_label="A-01")
		with patch_bin_loading(bin_doc), patch.object(bin_doc, "save") as save:
			setup.save()
		self.assertEqual(bin_doc.bin_label, "A-01")
		save.assert_called_once()
		self.assertEqual(setup.bin_table, [])
		self.assertEqual(frappe.db.count("Bin List", {"parent": "Bin Setup"}), 0)

	def test_rows_without_new_label_are_ignored(self):
		setup = self.make_setup(new_label="")
		with patch_bin_loading():
			setup.save()
		self.assertEqual(setup.bin_table, [])

	def test_missing_bin_is_skipped(self):
		item = frappe.get_doc(
			{"doctype": "Item", "item_code": "_Test Bin Setup Item", "item_group": "Products"}
		)
		item.insert(ignore_if_duplicate=True)
		setup = self.make_setup(item_code=item.name, new_label="B-02")
		with patch_bin_loading():
			setup.save()
		self.assertEqual(setup.bin_table, [])
