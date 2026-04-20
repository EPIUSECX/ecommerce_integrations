# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.integrations.utils import make_request
from frappe.utils import cint

from ecommerce_integrations.shopify.connection import get_shopify_access_token, handle_shopify_api_auth_error
from ecommerce_integrations.shopify.constants import (
	API_VERSION,
	ORDER_ID_FIELD,
	SETTING_DOCTYPE,
	SHOPIFY_LINE_ITEM_ID_FIELD,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def push_fulfillment_from_dn(doc, method=None):
	"""Doc event: enqueue Shopify fulfillment when a Delivery Note is submitted."""
	if getattr(doc, "is_return", False) or doc.docstatus != 1:
		return

	setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	if not setting.is_enabled() or not cint(getattr(setting, "push_fulfillment_to_shopify", 0)):
		return

	if not any(getattr(d, "against_sales_order", None) for d in doc.items):
		return

	frappe.enqueue(
		"ecommerce_integrations.shopify.fulfillment_push.push_shopify_fulfillment_for_delivery_note",
		queue="short",
		delivery_note_name=doc.name,
	)


def push_shopify_fulfillment_for_delivery_note(delivery_note_name: str) -> None:
	"""POST a fulfillment to Shopify for the given Delivery Note (async worker)."""
	if frappe.flags.in_test:
		return

	frappe.set_user("Administrator")
	dn = frappe.get_doc("Delivery Note", delivery_note_name)
	if dn.docstatus != 1 or getattr(dn, "is_return", False):
		return

	setting = frappe.get_doc(SETTING_DOCTYPE)
	if not setting.is_enabled() or not cint(getattr(setting, "push_fulfillment_to_shopify", 0)):
		return

	sales_orders = {d.against_sales_order for d in dn.items if d.against_sales_order}
	if len(sales_orders) != 1:
		create_shopify_log(
			status="Invalid",
			message=_("Delivery Note must reference exactly one Sales Order for Shopify fulfillment push."),
		)
		return

	so = frappe.get_doc("Sales Order", list(sales_orders)[0])
	order_id = so.get(ORDER_ID_FIELD)
	if not order_id:
		return

	line_items = []
	for d in dn.items:
		if not d.so_detail:
			continue
		lid = frappe.db.get_value("Sales Order Item", d.so_detail, SHOPIFY_LINE_ITEM_ID_FIELD)
		if lid:
			line_items.append({"id": int(lid), "quantity": cint(d.qty)})

	if not line_items:
		create_shopify_log(
			status="Invalid",
			message=_("No Shopify line item ids on Sales Order items; cannot build fulfillment."),
		)
		return

	warehouse = next((d.warehouse for d in dn.items if d.warehouse), None)
	location_id = _resolve_shopify_location_id(setting, warehouse)
	if not location_id:
		create_shopify_log(status="Error", message=_("Could not resolve Shopify location for fulfillment."))
		return

	token = get_shopify_access_token(setting)
	if not token:
		create_shopify_log(status="Invalid", message=_("Shopify access token is not configured."))
		return
	url = f"https://{setting.shopify_url.rstrip('/')}/admin/api/{API_VERSION}/orders/{order_id}/fulfillments.json"
	headers = {
		"X-Shopify-Access-Token": token,
		"Content-Type": "application/json",
		"Accept": "application/json",
	}
	payload = {
		"fulfillment": {
			"location_id": int(location_id),
			"tracking_number": (dn.lr_no or "")[:255],
			"notify_customer": True,
			"line_items": line_items,
		}
	}

	try:
		make_request("POST", url, headers=headers, json=payload)
	except Exception as e:
		handle_shopify_api_auth_error(e)
		create_shopify_log(status="Error", exception=e)
		return

	create_shopify_log(status="Success", message=_("Fulfillment pushed for Delivery Note {0}").format(dn.name))


def _resolve_shopify_location_id(setting, warehouse: str | None) -> str | None:
	if not warehouse:
		return None
	for row in setting.shopify_warehouse_mapping or []:
		if row.erpnext_warehouse == warehouse:
			return str(row.shopify_location_id)
	return None
