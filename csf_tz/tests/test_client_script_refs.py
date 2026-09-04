"""Every server method referenced by a csf_tz Desk client script must exist and be whitelisted."""

import glob
import os
import re

import frappe
from frappe.tests import IntegrationTestCase

from csf_tz import hooks

APP_PATH = frappe.get_app_path("csf_tz")
METHOD_PATTERN = re.compile(
	r"""(?:\bmethod\b|\bquery\b)\s*['"]?\s*:\s*['"]([\w.]+\.[\w]+)['"]"""
	r"""|frappe\.(?:call|xcall)\(\s*['"]([\w.]+\.[\w]+)['"]"""
)
KNOWN_BROKEN = {}


def client_script_files():
	files = set(glob.glob(os.path.join(APP_PATH, "public", "js", "*.js")))
	for scripts in (*hooks.doctype_js.values(), *hooks.doctype_list_js.values()):
		for script in scripts if isinstance(scripts, list) else [scripts]:
			files.add(os.path.join(APP_PATH, script))
	return sorted(files)


def referenced_methods(path):
	with open(path) as script:
		content = script.read()
	return sorted({match.group(1) or match.group(2) for match in METHOD_PATTERN.finditer(content)})


def resolve_error(method):
	try:
		frappe.is_whitelisted(frappe.get_attr(method))
	except Exception as error:
		return f"{type(error).__name__}: {str(error)[:120]}"
	return None


class TestClientScriptReferences(IntegrationTestCase):
	def test_hooked_script_files_exist(self):
		missing = [path for path in client_script_files() if not os.path.exists(path)]
		self.assertEqual(missing, [])

	def test_scan_finds_references(self):
		methods = {method for path in client_script_files() for method in referenced_methods(path)}
		self.assertIn("csf_tz.custom_api.get_item_info", methods)
		self.assertIn("csf_tz.csftz_hooks.payroll.update_slips", methods)

	def test_every_referenced_method_resolves_and_is_whitelisted(self):
		broken = []
		for path in client_script_files():
			relative = os.path.relpath(path, APP_PATH)
			if relative in KNOWN_BROKEN:
				continue
			for method in referenced_methods(path):
				error = resolve_error(method)
				if error:
					broken.append(f"{relative}: {method} -> {error}")
		self.assertEqual(broken, [], "\n".join(broken))

	def test_known_broken_scripts_are_still_broken(self):
		for relative in KNOWN_BROKEN:
			errors = [
				resolve_error(method) for method in referenced_methods(os.path.join(APP_PATH, relative))
			]
			self.assertTrue(any(errors), f"{relative} resolves now; remove it from KNOWN_BROKEN")
