frappe.ui.form.on("BOM", {
    refresh: function (frm) {
        if (!frm.fields_dict.additional_costs) return;
        frm.set_query("expense_account", "additional_costs", function () {
            return {
                filters: {
                    account_type: [
                        "in",
                        [
                            "Tax",
                            "Chargeable",
                            "Income Account",
                            "Expenses Included In Valuation",
                            "Expenses Included In Asset Valuation",
                        ],
                    ],
                    company: frm.doc.company,
                },
            };
        });
    },

});
