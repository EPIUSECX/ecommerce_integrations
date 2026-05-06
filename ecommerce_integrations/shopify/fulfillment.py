import frappe
from erpnext.selling.doctype.sales_order.sales_order import create_pick_list
from frappe.utils import cint, cstr

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from ecommerce_integrations.shopify.order import get_sales_order
from ecommerce_integrations.shopify.utils import create_shopify_log


def prepare_delivery_note(payload, request_id=None):
	frappe.set_user("Administrator")
	setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id

	order = payload

	try:
		sales_order = get_sales_order(cstr(order["id"]))
		if sales_order:
			create_delivery_note(order, setting, sales_order)
			create_shopify_log(status="Success")
		else:
			create_shopify_log(status="Invalid", message="Sales Order not found for syncing delivery note.")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_delivery_note(shopify_order, setting, so):
	if not cint(setting.sync_delivery_note):
		return

	if shopify_order.get("fulfillments") and so.docstatus == 1:
		create_draft_pick_list(so)


def create_draft_pick_list(so):
	if get_existing_pick_list(so.name):
		return

	pick_list = create_pick_list(so.name)
	pick_list.flags.ignore_mandatory = True
	pick_list.insert(ignore_permissions=True)


def get_existing_pick_list(sales_order):
	return frappe.db.get_value(
		"Pick List Item",
		{"sales_order": sales_order, "docstatus": ("<", 2)},
		"parent",
	)
