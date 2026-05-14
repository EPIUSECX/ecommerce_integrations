# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from frappe import _
from frappe.utils import cint, cstr, flt

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
			_apply_refund_line_items_to_return(cn, refund_line_items, getattr(setting, "shipping_item", None))
		_apply_refunded_shipping_to_return(cn, refund, setting)
		cn.flags.ignore_mandatory = True
		cn.insert(ignore_permissions=True)
		cn.submit()
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)
	else:
		create_shopify_log(status="Success")


def _apply_refund_line_items_to_return(cn, refund_line_items: list, shipping_item: str | None = None) -> None:
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
		if shipping_item and row.item_code == shipping_item:
			continue

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


def _apply_refunded_shipping_to_return(cn, refund: dict, setting) -> None:
	"""Remove copied Shopify shipping charges unless the refund includes shipping."""
	if _has_refunded_shipping(refund):
		return

	shipping_item = getattr(setting, "shipping_item", None)
	if shipping_item:
		for row in list(cn.items):
			if row.item_code == shipping_item:
				cn.remove(row)

	_remove_shipping_taxes(cn, setting)
	cn.run_method("calculate_taxes_and_totals")


def _has_refunded_shipping(refund: dict) -> bool:
	for shipping_line in refund.get("refund_shipping_lines") or []:
		if _get_money_amount(shipping_line, "subtotal_amount", "amount"):
			return True

	for adjustment in refund.get("order_adjustments") or []:
		kind = cstr(adjustment.get("kind")).lower()
		if kind in {"shipping_refund", "refund_shipping"} and _get_adjustment_amount(adjustment):
			return True

	return False


def _get_adjustment_amount(adjustment: dict) -> float:
	return abs(_get_money_amount(adjustment, "amount"))


def _get_money_amount(row: dict, *fields: str) -> float:
	for field in fields:
		amount = flt(row.get(field))
		if amount:
			return abs(amount)

		amount_set = row.get(f"{field}_set") or {}
		shop_money = amount_set.get("shop_money") or {}
		amount = flt(shop_money.get("amount"))
		if amount:
			return abs(amount)

	amount_set = row.get("amount_set") or {}
	shop_money = amount_set.get("shop_money") or {}
	return abs(flt(shop_money.get("amount")))


def _remove_shipping_taxes(cn, setting) -> None:
	shipping_item = getattr(setting, "shipping_item", None)
	shipping_account = getattr(setting, "default_shipping_charges_account", None)

	for tax in list(cn.get("taxes") or []):
		tax_detail = _get_item_wise_tax_detail(tax)
		if not tax_detail:
			is_shipping_charge = (
				tax.charge_type == "Actual" and shipping_account and tax.account_head == shipping_account
			)
			if is_shipping_charge:
				cn.remove(tax)
			continue

		if shipping_item and shipping_item in tax_detail:
			tax_detail.pop(shipping_item, None)
			if tax_detail:
				tax.item_wise_tax_detail = json.dumps(tax_detail)
				tax.tax_amount = _sum_item_wise_tax_amount(tax_detail)
			else:
				cn.remove(tax)


def _get_item_wise_tax_detail(tax) -> dict:
	tax_detail = tax.get("item_wise_tax_detail")
	if isinstance(tax_detail, dict):
		return tax_detail.copy()
	if not tax_detail:
		return {}
	return frappe.parse_json(tax_detail) or {}


def _sum_item_wise_tax_amount(tax_detail: dict) -> float:
	total = 0
	for detail in tax_detail.values():
		if isinstance(detail, list) and len(detail) > 1:
			total += flt(detail[1])
	return total
