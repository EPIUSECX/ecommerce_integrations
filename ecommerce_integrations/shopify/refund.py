# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from frappe import _
from frappe.utils import cint, cstr

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	SETTING_DOCTYPE,
	SHOPIFY_LINE_ITEM_ID_FIELD,
	SHOPIFY_REFUND_ID_FIELD,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def sync_refund(payload, request_id=None):
	"""Create Credit Note (Sales Invoice return) from Shopify ``refunds/create`` webhook payload."""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	refund = payload if isinstance(payload, dict) else {}
	refund_id = cstr(refund.get("id") or "")
	order_id = refund.get("order_id")

	if not order_id or not refund_id:
		create_shopify_log(status="Invalid", message=_("Refund payload missing id or order_id"))
		return

	if frappe.db.exists("Sales Invoice", {SHOPIFY_REFUND_ID_FIELD: refund_id, "is_return": 1}):
		create_shopify_log(status="Invalid", message=_("Refund already processed"))
		return

	si_name = frappe.db.get_value(
		"Sales Invoice",
		{ORDER_ID_FIELD: str(order_id), "docstatus": 1, "is_return": 0},
		"name",
	)
	if not si_name:
		create_shopify_log(status="Invalid", message=_("No submitted Sales Invoice for Shopify order"))
		return

	setting = frappe.get_doc(SETTING_DOCTYPE)
	if not setting.is_enabled():
		create_shopify_log(status="Invalid", message=_("Shopify integration is disabled"))
		return

	try:
		cn = make_sales_return(si_name)
		cn.set(SHOPIFY_REFUND_ID_FIELD, refund_id)
		refund_line_items = refund.get("refund_line_items") or []
		if refund_line_items and frappe.get_meta("Sales Invoice Item").has_field(SHOPIFY_LINE_ITEM_ID_FIELD):
			_apply_refund_line_items_to_return(cn, refund_line_items)
		cn.flags.ignore_mandatory = True
		cn.insert(ignore_permissions=True)
		cn.submit()
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)
	else:
		create_shopify_log(status="Success")


def _apply_refund_line_items_to_return(cn, refund_line_items: list) -> None:
	"""Adjust return quantities from Shopify refund line items (partial refunds)."""
	by_line = {
		str(r.get("line_item_id")): cint(r.get("quantity"))
		for r in refund_line_items
		if r.get("line_item_id") is not None
	}
	if not by_line:
		return

	rows_to_remove = []
	for row in list(cn.items):
		si_item = row.get("sales_invoice_item")
		if not si_item:
			rows_to_remove.append(row)
			continue
		lid = frappe.db.get_value("Sales Invoice Item", si_item, SHOPIFY_LINE_ITEM_ID_FIELD)
		if not lid or str(lid) not in by_line:
			rows_to_remove.append(row)
			continue
		row.qty = -1 * cint(by_line[str(lid)])

	for row in rows_to_remove:
		cn.remove(row)

	cn.run_method("calculate_taxes_and_totals")
