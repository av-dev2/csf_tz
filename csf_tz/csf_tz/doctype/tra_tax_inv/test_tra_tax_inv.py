# Copyright (c) 2025, Aakvatech and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestTRATAXInv(IntegrationTestCase):
	def test_tra_tax_inv_creation(self):
		doc = frappe.new_doc("TRA TAX Inv")
		doc.verification_code = "TEST123_123456"
		doc.type = "Sales"
		doc.verification_status = "Pending"
		doc.insert()

		self.assertTrue(doc.name.startswith("TRA-TAX-INV-"))
		self.assertEqual(doc.type, "Sales")
		self.assertEqual(doc.verification_status, "Pending")
		self.assertEqual(doc.verification_code, "TEST123_123456")
