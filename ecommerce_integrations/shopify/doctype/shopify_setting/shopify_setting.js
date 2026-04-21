// Copyright (c) 2021, Frappe and contributors
// For license information, please see LICENSE

frappe.provide("ecommerce_integrations.shopify.shopify_setting");

frappe.ui.form.on("Shopify Setting", {
	onload: function (frm) {
		frappe.call({
			method: "ecommerce_integrations.utils.naming_series.get_series",
			callback: function (r) {
				$.each(r.message, (key, value) => {
					set_field_options(key, value);
				});
			},
		});
		const params = new URLSearchParams(window.location.search || "");
		const oauth = params.get("shopify_oauth");
		if (oauth === "success") {
			frappe.show_alert({ message: __("Shopify connected successfully."), indicator: "green" });
			window.history.replaceState({}, document.title, window.location.pathname);
		} else if (oauth === "error") {
			const msg = params.get("shopify_oauth_message") || __("OAuth failed");
			frappe.msgprint({ title: __("Shopify OAuth"), message: msg, indicator: "red" });
			window.history.replaceState({}, document.title, window.location.pathname);
		}
	},

	connect_with_shopify: function (frm) {
		frappe.call({
			method: "ecommerce_integrations.shopify.oauth.shopify_oauth_start",
			freeze: true,
			freeze_message: __("Redirecting to Shopify…"),
			callback: function (r) {
				if (!r.exc && r.message) {
					window.location.href = r.message;
				}
			},
		});
	},

	fetch_shopify_locations: function (frm) {
		frappe.call({
			doc: frm.doc,
			method: "update_location_table",
			callback: (r) => {
				if (!r.exc) refresh_field("shopify_warehouse_mapping");
			},
		});
	},

	refresh: function (frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "ecommerce_integrations.shopify.connection.test_shopify_connection",
				freeze: true,
				freeze_message: __("Testing Shopify connection…"),
				callback: function (r) {
					if (!r.exc) {
						const shop = (r.message && r.message.shop) || frm.doc.shopify_url;
						frappe.show_alert(
							{ message: __("Connected to {0}", [shop]), indicator: "green" },
							7,
						);
						frm.reload_doc();
					}
				},
			});
		});
		frm.add_custom_button(__("Import Products"), function () {
			frappe.set_route("shopify-import-products");
		});
		frm.add_custom_button(__("Export Products"), () => {
			const start_export = () => {
				frappe.call({
					method: "ecommerce_integrations.shopify.product.export_all_products",
					freeze: true,
					freeze_message: __("Queuing ERPNext product export…"),
					callback: function (r) {
						if (!r.exc) {
							frappe.show_alert(
								{
									message: __("ERPNext product export queued. Check View Logs for progress."),
									indicator: "green",
								},
								7,
							);
						}
					},
				});
			};

			if (frm.is_dirty()) {
				frm.save().then(() => start_export());
			} else {
				start_export();
			}
		});
		frm.add_custom_button(__("View Logs"), () => {
			frappe.set_route("List", "Ecommerce Integration Log", {
				integration: "Shopify",
			});
		});
		frm.trigger("setup_queries");
	},

	setup_queries: function (frm) {
		const warehouse_query = () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
					disabled: 0,
				},
			};
		};
		frm.set_query("warehouse", warehouse_query);
		frm.set_query(
			"erpnext_warehouse",
			"shopify_warehouse_mapping",
			warehouse_query,
		);

		frm.set_query("price_list", () => {
			return {
				filters: {
					selling: 1,
				},
			};
		});

		frm.set_query("cost_center", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: "No",
				},
			};
		});

		frm.set_query("cash_bank_account", () => {
			return {
				filters: [
					["Account", "account_type", "in", ["Cash", "Bank"]],
					["Account", "root_type", "=", "Asset"],
					["Account", "is_group", "=", 0],
					["Account", "company", "=", frm.doc.company],
				],
			};
		});

		const tax_query = () => {
			return {
				query: "erpnext.controllers.queries.tax_account_query",
				filters: {
					account_type: ["Tax", "Chargeable", "Expense Account"],
					company: frm.doc.company,
				},
			};
		};

		frm.set_query("tax_account", "taxes", tax_query);
		frm.set_query("default_sales_tax_account", tax_query);
		frm.set_query("default_shipping_charges_account", tax_query);
	},
});
