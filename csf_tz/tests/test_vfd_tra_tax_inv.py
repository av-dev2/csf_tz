from unittest.mock import MagicMock, patch

import frappe
import requests
from bs4 import BeautifulSoup
from frappe.tests import IntegrationTestCase

from csf_tz.csf_tz.doctype.tra_tax_inv import tra_tax_inv as tra
from csf_tz.tests.vfd_test_records import COMPANY, ITEM

SESSION = "csf_tz.csf_tz.doctype.tra_tax_inv.tra_tax_inv.requests.Session"
CODE = "ABC123_102030"
FORM_HTML = '<html><body><form><input name="__RequestVerificationToken" value="tok123"/></form></body></html>'
RECEIPT_HTML = f"""<html><head><title>TRA Receipt</title></head><body>
<h4><b>ACME LTD</b></h4>
<div class="invoice-info">
TIN: 123456789
VRN: 40-123456-A
SERIAL NO: 10TZ100001
UIN: UIN123
TAX OFFICE: Ilala
MOBILE: 0712345678
P.O.BOX 123 DAR
</div>
<div class="invoice-header">
CUSTOMER NAME: John Doe
CUSTOMER ID TYPE: TIN
CUSTOMER ID: 987654321
CUSTOMER MOBILE: 0755000000
</div>
<div class="invoice-header">
RECEIPT NO: RCT-001
Z NUMBER: Z-77
RECEIPT DATE: 25/08/2026
RECEIPT TIME: 10:20:30
</div>
<table class="table table-striped"><thead><tr><th>Description</th><th>Qty</th><th>Amount</th></tr></thead>
<tbody><tr><td>{ITEM}</td><td>2</td><td>1,000.00</td></tr><tr><td>Unknown Widget</td><td>1</td><td>500.00</td></tr></tbody></table>
<table class="table"><tbody>
<tr><th>TOTAL EXCL OF TAX:</th><td>2,118.64</td></tr>
<tr><th>TOTAL TAX:</th><td>381.36</td></tr>
<tr><th>TOTAL INCL OF TAX:</th><td>2,500.00</td></tr>
<tr><th>TAX RATE A (18%)</th><td>381.36</td></tr>
</tbody></table>
<h4>RECEIPT VERIFICATION CODE</h4><h4>{CODE}</h4>
<img id="barcode" src="https://qr.test/?data=https://verify.tra.go.tz/{CODE}&size=1" title="https://verify.tra.go.tz/{CODE}"/>
</body></html>"""


def html_response(text, url="https://verify.tra.go.tz/Verify/Verified"):
	response = MagicMock()
	response.text = text
	response.status_code = 200
	response.url = url
	response.headers = {}
	response.cookies = {}
	return response


def mock_session(form_html=FORM_HTML, ask_for_time=True):
	session = MagicMock()
	session.headers = {}
	if ask_for_time:
		session.post.return_value = html_response("Please provide your Receipt time")
		session.get.side_effect = [html_response(form_html), html_response(RECEIPT_HTML)]
	else:
		session.post.return_value = html_response(RECEIPT_HTML)
		session.get.side_effect = [html_response(form_html)]
	return session


def make_tra_doc(code, items=None, **values):
	doc = frappe.get_doc(
		{
			"doctype": "TRA TAX Inv",
			"type": "Sales",
			"verification_code": code,
			"company_name": "ACME LTD",
			"customer_name": "John Doe",
			"grand_total": 200,
			"items": items if items is not None else [{"description": ITEM, "quantity": "2", "amount": 100}],
			**values,
		}
	)
	return doc.insert(ignore_links=True)


class TestVerifyTraReceipt(IntegrationTestCase):
	def verify(self, session=None, **kwargs):
		with patch(SESSION, return_value=session or mock_session()):
			return tra.verify_tra_receipt(**kwargs)

	def test_creates_verified_document_from_receipt(self):
		session = mock_session()
		result = self.verify(session, verification_code=CODE)
		self.assertTrue(result["success"], result)
		self.assertEqual(result["company_name"], "ACME LTD")
		self.assertEqual(result["receipt_number"], "RCT-001")
		self.assertEqual(result["total"], 2500)

		self.assertEqual(
			session.post.call_args.kwargs["data"], {"__RequestVerificationToken": "tok123", "RctVcode": CODE}
		)
		self.assertIn("Secret=10:20:30", session.get.call_args_list[1].args[0])

		doc = frappe.get_doc("TRA TAX Inv", result["doc_name"])
		self.assertEqual(doc.verification_status, "Verified")
		self.assertEqual(doc.type, "Sales")
		self.assertEqual(doc.customer_name, "John Doe")
		self.assertEqual(doc.customer_id_type, "TIN")
		self.assertEqual(doc.customer_id, "987654321")
		self.assertEqual(doc.customer_mobile, "0755000000")
		self.assertEqual(doc.subtotal, 2118.64)
		self.assertEqual(doc.total_tax, 381.36)
		self.assertEqual(doc.grand_total, 2500)
		self.assertEqual([row.description for row in doc.items], [ITEM, "Unknown Widget"])
		self.assertEqual(doc.items[0].quantity, "2")
		self.assertEqual(doc.items[0].amount, 1000)

	def test_duplicate_code_is_rejected(self):
		self.verify(verification_code="DUP123_102030")
		result = self.verify(verification_code="DUP123_102030")
		self.assertFalse(result["success"])
		self.assertIn("already exists", result["message"])

	def test_accepts_qr_code_url(self):
		result = self.verify(qr_code_data="https://verify.tra.go.tz/QRC123_102030")
		self.assertTrue(result["success"])
		self.assertEqual(result["verification_code"], "QRC123_102030")
		result = self.verify(verification_code="https://verify.tra.go.tz/URL123_102030")
		self.assertEqual(result["verification_code"], "URL123_102030")

	def test_rejects_invalid_input(self):
		result = tra.verify_tra_receipt(qr_code_data="nonsense")
		self.assertFalse(result["success"])
		self.assertEqual(result["message"], "Invalid QR code data format")
		result = tra.verify_tra_receipt()
		self.assertEqual(result["message"], "No verification code provided")

	def test_creates_failed_document_when_tra_is_unreachable(self):
		session = MagicMock()
		session.headers = {}
		session.get.side_effect = requests.exceptions.ConnectionError("offline")
		result = self.verify(session, verification_code="OFF123_102030")
		self.assertTrue(result["success"])
		doc = frappe.get_doc("TRA TAX Inv", result["doc_name"])
		self.assertEqual(doc.verification_status, "Failed")
		self.assertFalse(doc.items)

	def test_guest_cannot_create_documents(self):
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		result = self.verify(verification_code="GST123_102030")
		self.assertFalse(result["success"])
		self.assertIn("Failed to create document", result["message"])


class TestFetchTraVerification(IntegrationTestCase):
	def test_validates_verification_code_format(self):
		self.assertIn("does not contain time", tra.fetch_tra_verification("ABC123")["error"])
		self.assertIn("Invalid time format", tra.fetch_tra_verification("ABC123_12")["error"])

	def test_requires_form_token(self):
		with patch(SESSION, return_value=mock_session(form_html="<html></html>")):
			result = tra.fetch_tra_verification(CODE)
		self.assertEqual(result["error"], "Could not find verification token in form")

	def test_parses_receipt_without_time_prompt(self):
		session = mock_session(ask_for_time=False)
		with patch(SESSION, return_value=session):
			result = tra.fetch_tra_verification(CODE)
		self.assertEqual(session.get.call_count, 1)
		self.assertEqual(result["receipt_time_used"], "10:20:30")
		self.assertEqual(result["form_token_used"], "tok123")
		self.assertEqual(result["title"], "TRA Receipt")
		self.assertEqual(result["verification_data"]["html_content"], RECEIPT_HTML)
		self.assertEqual(len(result["verification_data"]["tables"]), 2)

	def test_request_errors_are_reported(self):
		session = MagicMock()
		session.headers = {}
		session.get.side_effect = requests.exceptions.Timeout("slow")
		with patch(SESSION, return_value=session):
			result = tra.fetch_tra_verification(CODE)
		self.assertIn("Request failed", result["error"])


class TestReceiptParsing(IntegrationTestCase):
	def test_extract_receipt_from_html(self):
		data = tra.extract_receipt_from_html(RECEIPT_HTML)
		self.assertEqual(data["company_info"]["name"], "ACME LTD")
		self.assertEqual(data["company_info"]["tin"], "123456789")
		self.assertEqual(data["company_info"]["vrn"], "40-123456-A")
		self.assertEqual(data["company_info"]["serial_number"], "10TZ100001")
		self.assertEqual(data["company_info"]["uin"], "UIN123")
		self.assertEqual(data["company_info"]["tax_office"], "Ilala")
		self.assertEqual(data["company_info"]["mobile"], "0712345678")
		self.assertEqual(data["company_info"]["address"], "P.O.BOX 123 DAR")
		self.assertEqual(
			data["receipt_info"],
			{"receipt_number": "RCT-001", "z_number": "Z-77", "date": "25/08/2026", "time": "10:20:30"},
		)
		self.assertEqual(
			data["totals"], {"subtotal": "2,118.64", "total_tax": "381.36", "grand_total": "2,500.00"}
		)
		self.assertEqual(data["taxes"], [{"label": "TAX RATE A (18%)", "amount": "381.36", "rate": "18%"}])
		self.assertEqual(data["verification_info"]["code"], CODE)
		self.assertEqual(data["verification_info"]["verification_url"], f"https://verify.tra.go.tz/{CODE}")
		self.assertEqual(data["verification_info"]["qr_code_data"], f"https://verify.tra.go.tz/{CODE}")

	def test_extract_verification_data(self):
		data = tra.extract_verification_data(BeautifulSoup(RECEIPT_HTML, "html.parser"))
		self.assertEqual(data["tables"][0][1], [ITEM, "2", "1,000.00"])
		self.assertIn("ACME LTD", data["all_text"])
		self.assertNotIn("form_inputs", data)
		form = tra.extract_verification_data(BeautifulSoup(FORM_HTML, "html.parser"))
		self.assertEqual(form["form_inputs"][0]["name"], "__RequestVerificationToken")

	def test_extract_receipt_data_prefers_html(self):
		data = tra.extract_receipt_data({"html_content": RECEIPT_HTML})
		self.assertEqual(data["receipt_info"]["receipt_number"], "RCT-001")
		self.assertEqual(tra.extract_receipt_data({})["items"], [])
		self.assertEqual(tra.extract_receipt_data({"status_elements": []})["totals"], {})

	def test_extract_receipt_data_from_tables(self):
		tables = [
			[["Description", "Qty", "Amount"], ["Widget", "1", "10"], ["", "", ""]],
			[
				["Total Excl", "100"],
				["Total Incl", "118"],
				["Tax Total", "18"],
				["VAT", "18"],
				["Receipt No", "R1"],
				["Date", "2026-08-25"],
				["Time", "10:00"],
				["TIN", "111"],
				["VRN", "222"],
			],
		]
		data = tra.extract_receipt_data({"tables": tables})
		self.assertEqual(data["items"], [{"description": "Widget", "quantity": "1", "amount": "10"}])
		self.assertEqual(data["totals"], {"subtotal": "100", "grand_total": "118", "total_tax": "18"})
		self.assertEqual(data["taxes"], [{"type": "VAT", "amount": "18"}])
		self.assertEqual(
			data["receipt_info"], {"receipt_number": "R1", "date": "2026-08-25", "time": "10:00"}
		)
		self.assertEqual(data["company_info"], {"tin": "111", "vrn": "222"})

	def test_create_tra_tax_inv_document(self):
		receipt = tra.extract_receipt_from_html(RECEIPT_HTML)
		result = tra.create_tra_tax_inv_document("NEW123_102030", receipt, {"url": "https://x"})
		self.assertTrue(result["success"])
		doc = frappe.get_doc("TRA TAX Inv", result["doc_name"])
		self.assertEqual(doc.type, "Purchase")
		self.assertEqual(doc.grand_total, 2500)
		self.assertEqual(len(doc.items), 2)
		duplicate = tra.create_tra_tax_inv_document("NEW123_102030", receipt, {})
		self.assertFalse(duplicate["success"])
		self.assertEqual(duplicate["existing_doc"], doc.name)

	def test_create_tra_tax_inv_document_safe_handles_bad_amounts(self):
		receipt = {
			"totals": {"subtotal": "n/a", "grand_total": "1,0"},
			"items": [{"description": "X", "amount": "?"}],
		}
		result = tra.create_tra_tax_inv_document_safe("BAD123_102030", receipt, {"success": False})
		self.assertTrue(result["success"])
		doc = frappe.get_doc("TRA TAX Inv", result["doc_name"])
		self.assertEqual(doc.verification_status, "Failed")
		self.assertEqual(doc.subtotal, 0)
		self.assertEqual(doc.grand_total, 10)
		self.assertEqual(doc.items[0].amount, 0)
		self.assertEqual(result["items_count"], 1)


class TestCreateInvoiceFromTraTaxInv(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.defaults.set_user_default("company", COMPANY)
		frappe.clear_cache(user="Administrator")
		assert frappe.defaults.get_user_default("Company") == COMPANY

	def test_creates_sales_invoice_and_customer(self):
		doc = make_tra_doc("SI0001_102030")
		result = tra.create_invoice_from_tra_tax_inv(doc.name, "Sales Invoice")
		self.assertTrue(result["success"], result)
		invoice = frappe.get_doc("Sales Invoice", result["invoice_name"])
		self.assertEqual(invoice.customer_name, "ACME LTD")
		self.assertEqual(invoice.items[0].item_code, ITEM)
		self.assertEqual(invoice.items[0].qty, 2)
		self.assertEqual(invoice.items[0].rate, 100)
		self.assertEqual(invoice.docstatus, 0)
		doc.reload()
		self.assertEqual(doc.reference_doctype, "Sales Invoice")
		self.assertEqual(doc.reference_docname, invoice.name)
		again = tra.create_invoice_from_tra_tax_inv(doc.name, "Sales Invoice")
		self.assertFalse(again["success"])
		self.assertIn("Invoice already created", again["message"])

	def test_creates_purchase_invoice_and_supplier(self):
		doc = make_tra_doc("PI0001_102030", type="Purchase", receipt_number="RCT-9")
		result = doc.create_purchase_invoice()
		self.assertTrue(result["success"], result)
		invoice = frappe.get_doc("Purchase Invoice", result["invoice_name"])
		self.assertEqual(invoice.supplier_name, "John Doe")
		self.assertEqual(invoice.bill_no, "RCT-9")
		self.assertEqual(invoice.items[0].item_code, ITEM)
		self.assertTrue(frappe.db.exists("Supplier", {"supplier_name": "John Doe"}))

	def test_document_method_creates_sales_invoice(self):
		doc = make_tra_doc("SI0002_102030")
		self.assertTrue(doc.create_sales_invoice()["success"])

	def test_validation_reports_missing_masters(self):
		empty = make_tra_doc("EMP001_102030", items=[])
		result = tra.create_invoice_from_tra_tax_inv(empty.name, "Sales Invoice")
		self.assertIn("No items found", result["message"])

		missing = make_tra_doc(
			"MIS001_102030",
			items=[
				{"description": "Unknown Widget"},
				{"description": "Mapped", "mapped_item_code": "_Missing Item"},
			],
		)
		result = tra.create_invoice_from_tra_tax_inv(missing.name, "Sales Invoice")
		self.assertFalse(result["success"])
		self.assertEqual(result["missing_items"], ["Unknown Widget", "Mapped (mapped to: _Missing Item)"])

		no_company = make_tra_doc("NOC001_102030", company_name="")
		result = tra.create_invoice_from_tra_tax_inv(no_company.name, "Sales Invoice")
		self.assertIn("Customer: No company name", result["missing_party"])
		no_customer = make_tra_doc("NOS001_102030", customer_name="")
		result = tra.create_invoice_from_tra_tax_inv(no_customer.name, "Purchase Invoice")
		self.assertIn("Supplier: No customer name", result["missing_party"])

	def test_invalid_invoice_type(self):
		doc = make_tra_doc("TYP001_102030")
		result = tra.create_invoice_from_tra_tax_inv(doc.name, "Journal Entry")
		self.assertIn("Invalid invoice type", result["message"])

	def test_user_without_permission_cannot_create_invoice(self):
		doc = make_tra_doc("PRM001_102030")
		user = "vfd-noperm@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "No Perm", "send_welcome_email": 0}
			).insert()
		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")
		result = tra.create_invoice_from_tra_tax_inv(doc.name, "Sales Invoice")
		self.assertFalse(result["success"])

	def test_get_or_suggest_item(self):
		self.assertEqual(tra.get_or_suggest_item(frappe._dict(mapped_item_code=ITEM, description="x")), ITEM)
		self.assertEqual(
			tra.get_or_suggest_item(frappe._dict(mapped_item_code="_Nope", description=ITEM)), ITEM
		)
		self.assertEqual(tra.get_or_suggest_item(frappe._dict(description="_Test Item")), "_Test Item")
		self.assertEqual(tra.get_or_suggest_item(frappe._dict(description="Unknown")), "Unknown")
		self.assertIsNone(tra.get_or_suggest_item(frappe._dict(description="")))

	def test_get_or_create_party(self):
		self.assertIsNone(tra.get_or_create_customer(""))
		self.assertIsNone(tra.get_or_create_supplier(""))
		self.assertEqual(tra.get_or_create_customer("_Test Customer"), "_Test Customer")
		self.assertEqual(tra.get_or_create_supplier("_Test Supplier"), "_Test Supplier")
		created = tra.get_or_create_customer("Brand New Customer")
		customer_group = frappe.db.get_value("Customer", created, "customer_group")
		self.assertEqual(frappe.db.get_value("Customer Group", customer_group, "is_group"), 0)
		self.assertEqual(tra.get_or_create_customer("Brand New Customer"), created)
		supplier = tra.get_or_create_supplier("Brand New Supplier")
		self.assertEqual(frappe.db.get_value("Supplier", supplier, "supplier_type"), "Company")

	def test_get_or_suggest_party(self):
		self.assertIsNone(tra.get_or_suggest_customer(""))
		self.assertIsNone(tra.get_or_suggest_supplier(""))
		self.assertEqual(tra.get_or_suggest_customer("_Test Customer"), "_Test Customer")
		self.assertEqual(tra.get_or_suggest_customer("Ghost"), "Ghost")
		self.assertEqual(tra.get_or_suggest_supplier("_Test Supplier"), "_Test Supplier")
		self.assertEqual(tra.get_or_suggest_supplier("Ghost"), "Ghost")
