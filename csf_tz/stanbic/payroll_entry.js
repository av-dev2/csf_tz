const buttonName = "Generate Stanbic Payments Initiation";

frappe.ui.form.on("Payroll Entry", {
	refresh: function (frm) {
		if (frm.doc.docstatus == 1) generate_payments_initiation(frm);
		else {
			frm.remove_custom_button(__(buttonName));
			// add_generate_payments_initiation_button(frm);
		}
	},
});

function generate_payments_initiation(frm) {
	let condition = true;
	frappe.db
		.get_list("Salary Slip", {
			fields: ["name", "docstatus"],
			filters: {
				payroll_entry: frm.doc.name,
			},
		})
		.then((res) => {
			if (res.length == 0) condition = false;
			// loop through the salary slips and check if all are submitted
			res.forEach((slip) => {
				if (slip.docstatus != 1) {
					condition = false;
				}
			});
			// if all salary slips are submitted, add custom button
			if (condition == true) {
				add_generate_payments_initiation_button(frm);
			} else {
				frm.remove_custom_button(__(buttonName));
			}
		});
}

function add_generate_payments_initiation_button(frm) {
	frm.add_custom_button(__(buttonName), function () {
		frappe.call({
			method: "csf_tz.stanbic.payments.make_payments_initiation",
			args: {
				payroll_entry_name: frm.doc.name,
				currency: frm.doc.currency,
			},
			callback: function (r) {
				if (r.message) {
					console.log(r.message);
				}
			},
		});
	});
}
