import frappe
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from csf_tz.custom_api import (
	app_error_log,
	generate_qrcode,
	get_pending_material_request,
	get_stock_balance_for,
	make_stock_reconciliation_for_all_pending_material_request,
	print_out,
)
from csf_tz.tests.custom_api_helpers import (
	COMPANY,
	WAREHOUSE,
	add_stock,
	disable_db_commit,
	make_test_item,
	set_csf_settings,
)


class TestSmallHelpers(IntegrationTestCase):
	"""QR code, error log and console helpers."""

	def setUp(self):
		disable_db_commit(self)

	def test_generate_qrcode(self):
		self.assertTrue(generate_qrcode("hello").startswith("data:image/png;base64,"))

	def test_app_error_log(self):
		self.assertIsNone(app_error_log("title", "error"))

	def test_print_out_variants(self):
		self.assertIsNone(print_out(None))
		frappe.local.message_log = []
		print_out("plain text", alert=True)
		self.assertIn("plain text", frappe.local.message_log[0]["message"])
		print_out(["a", 1, 2.5, {"k": "v"}, frappe._dict(k="v"), self], add_traceback=True)
		print_out("_CSF print_out to error log", to_error_log=True)
		self.assertTrue(frappe.db.exists("Error Log", {"method": "_CSF print_out to error log"}))


class TestTotalNetWeight(IntegrationTestCase):
	"""Stock Entry validate hook sums row weights."""

	def test_total_net_weight(self):
		entry = make_stock_entry(
			item_code="_Test Item", qty=1, rate=100, to_warehouse=WAREHOUSE, company=COMPANY, do_not_save=True
		)
		entry.items[0].total_weight = 5.5
		entry.insert()
		self.assertEqual(entry.total_net_weight, 5.5)
		entry.items[0].total_weight = 0
		entry.save()
		self.assertEqual(entry.total_net_weight, 0)


class TestStockReconciliationForMaterialRequests(IntegrationTestCase):
	"""Weekly job drafts a Stock Reconciliation for pending Material Requests."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_test_item("_CSF MR Reco Item")
		add_stock(cls.item.name, qty=5, rate=20)
		cls.request = frappe.get_doc(
			doctype="Material Request",
			material_request_type="Purchase",
			company=COMPANY,
			transaction_date=nowdate(),
			schedule_date=add_days(nowdate(), 7),
			items=[
				{
					"item_code": cls.item.name,
					"qty": 3,
					"warehouse": WAREHOUSE,
					"schedule_date": add_days(nowdate(), 7),
				}
			],
		).insert()
		cls.request.submit()

	def setUp(self):
		disable_db_commit(self)
		set_csf_settings(auto_stock_reconciliation=1)

	def test_stock_balance_and_pending_requests(self):
		self.assertEqual(
			get_stock_balance_for(self.item.name, WAREHOUSE), {"qty": 5, "rate": 20, "serial_nos": ""}
		)
		self.assertIn(self.request.name, [row.name for row in get_pending_material_request()])

	def test_reconciliation_is_created(self):
		make_stock_reconciliation_for_all_pending_material_request()
		reconciliation_name = frappe.db.get_value(
			"Material Request Item", self.request.items[0].name, "stock_reconciliation"
		)
		self.assertTrue(reconciliation_name)
		reconciliation = frappe.get_doc("Stock Reconciliation", reconciliation_name)
		self.assertEqual(reconciliation.docstatus, 0)
		row = reconciliation.items[0]
		self.assertEqual(
			(row.item_code, row.warehouse, row.qty, row.valuation_rate), (self.item.name, WAREHOUSE, 8, 20)
		)
		drafts = frappe.db.count("Stock Reconciliation", {"docstatus": 0})
		make_stock_reconciliation_for_all_pending_material_request()
		self.assertEqual(frappe.db.count("Stock Reconciliation", {"docstatus": 0}), drafts)

	def test_disabled_setting_is_noop(self):
		set_csf_settings(auto_stock_reconciliation=0)
		drafts = frappe.db.count("Stock Reconciliation", {"docstatus": 0})
		make_stock_reconciliation_for_all_pending_material_request()
		self.assertEqual(frappe.db.count("Stock Reconciliation", {"docstatus": 0}), drafts)
