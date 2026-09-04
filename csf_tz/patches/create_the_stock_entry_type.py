import frappe


def execute():
	stock_entry_type_list = [
		{"name": "To Company", "purpose": "Material Receipt"},
		{"name": "From Company", "purpose": "Material Issue"},
	]

	for stock_entry_type_data in stock_entry_type_list:
		if frappe.db.exists("Stock Entry Type", stock_entry_type_data["name"]):
			continue
		stock_entry_doc = frappe.new_doc("Stock Entry Type")
		stock_entry_doc.name = stock_entry_type_data["name"]
		stock_entry_doc.purpose = stock_entry_type_data["purpose"]
		stock_entry_doc.insert(ignore_permissions=True)
		stock_entry_doc.save()
