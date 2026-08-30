from importlib import import_module as real_import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.database.database import Database
from frappe.tests import IntegrationTestCase

import csf_tz
from csf_tz.config import accounts, desktop, docs, purchase_and_stock_management, sales_and_marketing, stock
from csf_tz.config import csf_tz as csf_tz_config
from csf_tz.monkey_patches.db_transaction_writes import check_transaction_status


def fake_db(transaction_writes=0, auto_commit_on_many_writes=0):
	return SimpleNamespace(
		transaction_writes=transaction_writes,
		auto_commit_on_many_writes=auto_commit_on_many_writes,
		commit=MagicMock(),
	)


class TestConfig(IntegrationTestCase):
	def test_module_config_get_data(self):
		for module in (accounts, csf_tz_config, purchase_and_stock_management, sales_and_marketing, stock):
			with self.subTest(module=module.__name__):
				data = module.get_data()
				self.assertIsInstance(data, list)
				for section in data:
					self.assertIn("label", section)
					self.assertIsInstance(section["items"], list)
					for item in section["items"]:
						self.assertIn(item["type"], ("doctype", "report", "page"))
						self.assertIn("name", item)

	def test_desktop_and_docs(self):
		modules = {entry["module_name"] for entry in desktop.get_data()}
		self.assertEqual(
			modules,
			{
				"CSF TZ",
				"Purchase and Stock Management",
				"Sales and Marketing",
				"VFD Providers",
				"VFD Settings",
			},
		)
		context = frappe._dict()
		docs.get_context(context)
		self.assertEqual(context.brand_html, "CSF TZ")


class TestTransactionWritesMonkeyPatch(IntegrationTestCase):
	def test_patch_is_installed(self):
		self.assertIs(Database.check_transaction_status, check_transaction_status)

	def test_implicit_commit_statements_raise_after_writes(self):
		db = fake_db(transaction_writes=1)
		for statement in (
			"ALTER TABLE x",
			"drop table x",
			"create table x",
			"truncate x",
			"START TRANSACTION",
		):
			with self.assertRaisesRegex(Exception, "implicit commit"):
				check_transaction_status(db, statement)
		check_transaction_status(fake_db(transaction_writes=0), "ALTER TABLE x")

	def test_commit_resets_counter_and_writes_are_counted(self):
		db = fake_db(transaction_writes=5)
		check_transaction_status(db, "commit")
		self.assertEqual(db.transaction_writes, 0)
		check_transaction_status(db, "select 1")
		self.assertEqual(db.transaction_writes, 0)
		check_transaction_status(db, "UPDATE t set a=1")
		check_transaction_status(db, "insert into t values (1)")
		check_transaction_status(db, "delete from t")
		self.assertEqual(db.transaction_writes, 3)
		check_transaction_status(db, "rollback")
		self.assertEqual(db.transaction_writes, 0)

	def test_too_many_writes(self):
		with patch.dict(frappe.conf, {"_max_writes_allowed": 2}):
			db = fake_db(transaction_writes=2)
			with self.assertRaisesRegex(frappe.ValidationError, "Too many writes"):
				check_transaction_status(db, "update t set a=1")

			db = fake_db(transaction_writes=2, auto_commit_on_many_writes=1)
			check_transaction_status(db, "update t set a=1")
			db.commit.assert_called_once()


class TestAppInit(IntegrationTestCase):
	def test_hooks_and_connect_are_wrapped(self):
		self.assertIs(frappe.get_hooks, csf_tz.get_hooks)
		self.assertIs(frappe.connect, csf_tz.connect)
		self.assertTrue(csf_tz.patches_loaded)
		self.assertIn("csf_tz", frappe.get_hooks("app_name"))

	def test_load_monkey_patches_guards(self):
		with patch.object(csf_tz, "patches_loaded", False):
			with patch.object(frappe.local, "site", None):
				csf_tz.load_monkey_patches()
			self.assertFalse(csf_tz.patches_loaded)

			with patch("frappe.get_installed_apps", return_value=["frappe"]):
				csf_tz.load_monkey_patches()
			self.assertFalse(csf_tz.patches_loaded)

			imported = []

			def fake_import(name, *args, **kwargs):
				if name.startswith("csf_tz.monkey_patches."):
					imported.append(name)
					return None
				return real_import_module(name, *args, **kwargs)

			with patch("importlib.import_module", side_effect=fake_import):
				csf_tz.load_monkey_patches()
			self.assertTrue(csf_tz.patches_loaded)
			self.assertEqual(imported, ["csf_tz.monkey_patches.db_transaction_writes"])

			with patch("importlib.import_module", side_effect=fake_import):
				csf_tz.load_monkey_patches()
			self.assertEqual(len(imported), 1)

	def test_console_publishes_to_current_user(self):
		with patch("frappe.publish_realtime") as publish:
			csf_tz.console("a", 1)
		publish.assert_called_once_with("out_to_console", ("a", 1), user=frappe.session.user)
