// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Order Track', {
	refresh: function(frm) {
		frm.events.show_hide_fields(frm);

		if (frm.doc.arrival_date && (!frm.doc.clearing_company || !frm.doc.expected_clearing_completion_date)) {
			frappe.msgprint(__("Either Clearing Company or Clearing Completion Date is unfilled, please fill the fields"));
		}
	},

	show_hide_fields: function(frm) {
		const has_supplier = Boolean(frm.doc.supplier && frm.doc.supplier_type);
		frm.toggle_display('international_supplier', has_supplier && frm.doc.supplier_type == 'International Supplier');
		frm.toggle_display('section_status', has_supplier);
	},

	supplier: function(frm) {
		frm.events.show_hide_fields(frm);
	},

	supplier_type: function(frm) {
		frm.events.show_hide_fields(frm);
	},
});
