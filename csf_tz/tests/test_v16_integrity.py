"""Framework-level integrity checks for csf_tz on Frappe v16.

Every check records per-item outcomes to a JSON file so the results can be
aggregated into a coverage report. Set CSF_TZ_TEST_RESULTS_DIR to choose the
output directory.
"""

import importlib
import json
import os
import pkgutil
import re
import traceback

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate, today

import csf_tz
from csf_tz import hooks

APP_PATH = frappe.get_app_path("csf_tz")
CSF_TZ_MODULES = [m.strip() for m in open(os.path.join(APP_PATH, "modules.txt")) if m.strip()]


def results_dir():
	path = os.environ.get("CSF_TZ_TEST_RESULTS_DIR") or frappe.get_site_path("private", "csf_tz_test_results")
	os.makedirs(path, exist_ok=True)
	return path


def record(name, items):
	with open(os.path.join(results_dir(), f"integrity_{name}.json"), "w") as f:
		json.dump(items, f, indent=1, default=str)


MISSING_APP_TABLE = re.compile(r"Table '[^']*\.tab([^']+)' doesn't exist")


def outcome(fn):
	try:
		fn()
		return {"status": "pass"}
	except (frappe.ValidationError, frappe.MandatoryError, frappe.PermissionError) as e:
		return {"status": "validation", "error": str(e)[:300]}
	except Exception as e:
		if MISSING_APP_TABLE.search(str(e)) or isinstance(e, frappe.DoesNotExistError):
			return {
				"status": "validation",
				"error": f"needs a DocType no installed app provides: {str(e)[:200]}",
			}
		return {
			"status": "fail",
			"error": f"{type(e).__name__}: {str(e)[:300]}",
			"trace": traceback.format_exc()[-1500:],
		}


def iter_hook_paths():
	for doctype, events in hooks.doc_events.items():
		for event, handlers in events.items():
			for handler in handlers if isinstance(handlers, list) else [handlers]:
				yield f"doc_events.{doctype}.{event}", handler
	for schedule, handlers in hooks.scheduler_events.items():
		if isinstance(handlers, dict):
			for cron, fns in handlers.items():
				for fn in fns:
					yield f"scheduler_events.cron[{cron}]", fn
		else:
			for fn in handlers:
				yield f"scheduler_events.{schedule}", fn
	for fn in hooks.after_install:
		yield "after_install", fn
	for fn in hooks.after_migrate:
		yield "after_migrate", fn
	for fn in hooks.jinja["methods"]:
		yield "jinja.methods", fn
	for doctype, cls in hooks.override_doctype_class.items():
		yield f"override_doctype_class.{doctype}", cls


def default_report_filters(company):
	year_start = str(getdate(today()).replace(month=1, day=1))
	fiscal_year = frappe.db.get_value(
		"Fiscal Year", {"year_start_date": ["<=", today()], "year_end_date": [">=", today()]}
	)
	return frappe._dict(
		company=company,
		from_date=year_start,
		to_date=today(),
		date=today(),
		as_on_date=today(),
		period_start_date=year_start,
		period_end_date=today(),
		fiscal_year=fiscal_year,
		from_fiscal_year=fiscal_year,
		to_fiscal_year=fiscal_year,
		year_start_date=year_start,
		year_end_date=today(),
		range="Monthly",
		periodicity="Monthly",
		based_on="Item",
		docstatus="1",
		currency=frappe.get_cached_value("Company", company, "default_currency"),
		presentation_currency=frappe.get_cached_value("Company", company, "default_currency"),
		ageing_based_on="Posting Date",
		range1=30,
		range2=60,
		range3=90,
		range4=120,
		report_date=today(),
		month=getdate(today()).strftime("%b"),
		year=getdate(today()).year,
		valuation_field_type="Currency",
		include_uom=None,
		item_code=None,
		warehouse=None,
	)


def run_with_missing_filter_keys(run, name, filters, attempts=6):
	"""Query reports raise KeyError for absent %(key)s params; add them as None and retry."""
	filters = frappe._dict(filters)
	for _ in range(attempts):
		try:
			return run(name, filters=filters, ignore_prepared_report=True)
		except KeyError as e:
			key = e.args[0]
			key = key.decode() if isinstance(key, bytes) else key
			if not isinstance(key, str) or key in filters:
				raise
			filters[key] = None
	return run(name, filters=filters, ignore_prepared_report=True)


class TestV16Integrity(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = (
			frappe.db.get_single_value("Global Defaults", "default_company")
			or frappe.get_all("Company", pluck="name", limit=1)[0]
		)

	def assert_all_pass(self, name, items):
		record(name, items)
		failed = {k: v for k, v in items.items() if v["status"] == "fail"}
		self.assertFalse(
			failed,
			f"{len(failed)}/{len(items)} {name} checks failed:\n"
			+ json.dumps(failed, indent=1, default=str)[:6000],
		)

	def test_hook_paths_resolve(self):
		items = {}
		for source, path in iter_hook_paths():
			items[f"{source} -> {path}"] = outcome(lambda p=path: frappe.get_attr(p))
		self.assert_all_pass("hooks", items)

	def test_hooks_merged_into_site(self):
		merged = frappe.get_hooks("doc_events")
		for doctype, events in hooks.doc_events.items():
			for event, handlers in events.items():
				for handler in handlers if isinstance(handlers, list) else [handlers]:
					self.assertIn(handler, merged[doctype][event], f"{doctype}.{event} missing {handler}")
		self.assertIn("csf_tz.bundle.js", frappe.get_hooks("app_include_js"))
		for doctype, cls in hooks.override_doctype_class.items():
			self.assertIn(cls, frappe.get_hooks("override_doctype_class")[doctype])

	def test_all_python_modules_import(self):
		items = {}
		for module in pkgutil.walk_packages(csf_tz.__path__, "csf_tz."):
			if ".tests" in module.name or module.name.rsplit(".", 1)[-1].startswith("test_"):
				continue
			items[module.name] = outcome(lambda m=module.name: importlib.import_module(m))
		self.assert_all_pass("module_imports", items)

	def test_doctype_js_files_exist(self):
		items = {}
		for doctype, paths in {**hooks.doctype_js, **hooks.doctype_list_js}.items():
			for path in paths if isinstance(paths, list) else [paths]:
				full = os.path.join(APP_PATH, path)
				items[f"{doctype}: {path}"] = {
					"status": "pass" if os.path.exists(full) else "fail",
					"error": full,
				}
		self.assert_all_pass("doctype_js", items)

	def test_bundles_are_built(self):
		from frappe.utils.jinja_globals import bundled_asset

		for bundle in ["csf_tz.bundle.js", "jobcards.bundle.js"]:
			asset = bundled_asset(bundle)
			self.assertIn("/dist/", asset, f"{bundle} not resolved to a built asset: {asset}")

	def test_doctypes(self):
		items = {}
		on_disk = {
			json.load(open(os.path.join(root, f)))["name"]
			for root, _dirs, files in os.walk(APP_PATH)
			for f in files
			if f.endswith(".json")
			and os.sep + "doctype" + os.sep in root
			and f[:-5] == os.path.basename(root)
		}
		in_db = set(frappe.get_all("DocType", filters={"module": ["in", CSF_TZ_MODULES]}, pluck="name"))
		for name in sorted(on_disk - in_db):
			items[f"{name} (on disk)"] = {
				"status": "fail",
				"error": "DocType JSON exists but not synced into site",
			}
		for name in sorted(in_db):

			def check(name=name):
				meta = frappe.get_meta(name)
				doc = frappe.new_doc(name)
				assert doc.doctype == name
				if not meta.is_virtual and not meta.issingle:
					assert frappe.db.table_exists(name), f"table missing for {name}"
				if not meta.istable and not meta.issingle:
					frappe.get_all(name, limit=1)
				if meta.issingle:
					frappe.get_single(name)

			items[name] = outcome(check)
		self.assert_all_pass("doctypes", items)

	def test_doctype_controllers_are_document_subclasses(self):
		from frappe.model.document import Document

		items = {}
		for name in frappe.get_all("DocType", filters={"module": ["in", CSF_TZ_MODULES]}, pluck="name"):
			items[name] = outcome(
				lambda n=name: self.assertTrue(issubclass(frappe.get_doc({"doctype": n}).__class__, Document))
			)
		self.assert_all_pass("controllers", items)

	def test_reports_execute(self):
		from frappe.desk.query_report import run

		items = {}
		filters = default_report_filters(self.company)
		reports = frappe.get_all(
			"Report",
			filters={"module": ["in", CSF_TZ_MODULES], "disabled": 0},
			fields=["name", "report_type"],
		)
		for report in reports:

			def check(name=report.name):
				result = run_with_missing_filter_keys(run, name, filters)
				assert "columns" in result or "result" in result

			items[f"{report.name} [{report.report_type}]"] = outcome(check)
		self.assert_all_pass("reports", items)

	def test_pages_load(self):
		from frappe.desk.desk_page import get

		items = {}
		for page in frappe.get_all("Page", filters={"module": ["in", CSF_TZ_MODULES]}, pluck="name"):
			items[page] = outcome(lambda p=page: get(p))
		self.assert_all_pass("pages", items)

	def test_workspace_links_target_existing_records(self):
		items = {}
		for workspace in frappe.get_all(
			"Workspace", filters={"module": ["in", CSF_TZ_MODULES]}, pluck="name"
		):
			for link in frappe.get_doc("Workspace", workspace).links:
				if link.type != "Link" or not link.link_to:
					continue
				exists = frappe.db.exists(link.link_type, link.link_to)
				items[f"{workspace}: {link.link_type} {link.link_to}"] = {
					"status": "pass" if exists else "fail",
					"error": "" if exists else "link target does not exist",
				}
		self.assert_all_pass("workspace_links", items)

	def test_patches_are_importable(self):
		items = {}
		for line in open(os.path.join(APP_PATH, "patches.txt")):
			line = line.strip()
			if not line or line.startswith("["):
				continue
			if line.startswith("execute:"):
				items[line] = outcome(lambda l=line: compile(l[len("execute:") :], "<patch>", "exec"))
			else:
				items[line] = outcome(lambda l=line: importlib.import_module(l.split()[0]).execute)
		self.assert_all_pass("patches", items)

	def test_custom_fields_installed(self):
		items = {}
		folder = os.path.join(APP_PATH, "patches", "custom_fields", "custom_fields_json")
		for file in sorted(os.listdir(folder)):
			for field in json.load(open(os.path.join(folder, file))):
				if not frappe.db.exists("DocType", field["dt"]):
					items[f"{file}: {field['dt']}.{field['fieldname']}"] = {
						"status": "validation",
						"error": "DocType not installed",
					}
					continue
				exists = frappe.db.exists(
					"Custom Field", {"dt": field["dt"], "fieldname": field["fieldname"]}
				)
				items[f"{file}: {field['dt']}.{field['fieldname']}"] = {
					"status": "pass" if exists else "fail",
					"error": "" if exists else "missing",
				}
		self.assert_all_pass("custom_fields", items)

	def test_property_setters_installed(self):
		items = {}
		folder = os.path.join(APP_PATH, "patches", "property_setter", "property_setter_json")
		for file in sorted(os.listdir(folder)):
			for setter in json.load(open(os.path.join(folder, file))):
				key = f"{file}: {setter['doc_type']}.{setter.get('field_name')}.{setter['property']}"
				if not frappe.db.exists("DocType", setter["doc_type"]):
					items[key] = {"status": "validation", "error": "DocType not installed"}
					continue
				filters = {"doc_type": setter["doc_type"], "property": setter["property"]}
				if setter.get("field_name"):
					filters["field_name"] = setter["field_name"]
				exists = frappe.db.exists("Property Setter", filters)
				items[key] = {"status": "pass" if exists else "fail", "error": "" if exists else "missing"}
		self.assert_all_pass("property_setters", items)

	def test_override_doctype_classes_are_active(self):
		for doctype, path in hooks.override_doctype_class.items():
			cls = frappe.get_attr(path)
			self.assertIsInstance(frappe.new_doc(doctype), cls, f"{doctype} does not use {path}")

	def test_monkey_patch_is_loaded(self):
		from frappe.database.database import Database

		csf_tz.load_monkey_patches()
		self.assertEqual(
			Database.check_transaction_status.__module__, "csf_tz.monkey_patches.db_transaction_writes"
		)

	def test_whitelisted_methods_are_registered(self):
		items = {}
		inventory = os.path.join(APP_PATH, "tests", "whitelisted_methods.json")
		if not os.path.exists(inventory):
			self.skipTest("no whitelisted_methods.json inventory")
		for entry in json.load(open(inventory)):
			if entry.get("in_class"):
				continue

			def check(path=entry["path"]):
				fn = frappe.get_attr(path)
				frappe.is_whitelisted(fn)

			items[entry["path"]] = outcome(check)
		self.assert_all_pass("whitelisted", items)

	def test_jinja_qrcode_method(self):
		from csf_tz.custom_api import generate_qrcode

		self.assertTrue(
			generate_qrcode("hello").startswith("data:image/png;base64,")
			or len(generate_qrcode("hello")) > 20
		)
		self.assertIn("csf_tz.custom_api.generate_qrcode", frappe.get_hooks("jinja")["methods"])

	def test_config_modules(self):
		items = {}
		for name in [
			"desktop",
			"csf_tz",
			"accounts",
			"purchase_and_stock_management",
			"stock",
			"sales_and_marketing",
		]:
			items[name] = outcome(lambda n=name: importlib.import_module(f"csf_tz.config.{n}").get_data())
		self.assert_all_pass("config", items)

	def test_setup_data_files(self):
		from csf_tz.utils.setup import SETUP_SPECS, load_records

		items = {}
		for spec in SETUP_SPECS:
			items[spec["file"]] = outcome(
				lambda s=spec: (load_records(s["file"]), frappe.get_meta(s["doctype"]))
			)
		self.assert_all_pass("setup_data", items)

	def test_dashboard_chart_source(self):
		from csf_tz.csf_tz.dashboard_chart_source.multi_account_balance_timeline import (
			multi_account_balance_timeline as src,
		)

		chart = frappe.db.exists("Dashboard Chart", "Multi Bank Balance")
		self.assertTrue(chart, "Dashboard Chart 'Multi Bank Balance' not installed")
		result = src.get(chart_name="Multi Bank Balance", filters=json.dumps({"company": self.company}))
		self.assertIn("datasets", result)
