# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

"""Shopify OAuth (authorization code) install for Shopify Setting.

See Shopify: https://shopify.dev/docs/apps/auth/oauth/getting-started
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import requests

import frappe
from frappe import _
from frappe.utils import get_url, get_url_to_form, password

from ecommerce_integrations.shopify import connection
from ecommerce_integrations.shopify.constants import (
	AUTH_METHOD_OAUTH,
	CONNECTION_STATUS_CONNECTED,
	SETTING_DOCTYPE,
	SHOPIFY_OAUTH_SCOPES,
)

OAUTH_STATE_CACHE_PREFIX = "shopify_oauth_state:"
OAUTH_STATE_TTL_SEC = 600


def _oauth_redirect(url: str) -> None:
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = url


def _redirect_to_form(**query_params) -> None:
	target = get_url_to_form(SETTING_DOCTYPE, SETTING_DOCTYPE)
	if query_params:
		sep = "&" if "?" in target else "?"
		target = target + sep + urlencode(query_params)
	_oauth_redirect(target)


@frappe.whitelist()
def shopify_oauth_start() -> str:
	"""Return Shopify authorize URL. Desk opens this URL in the browser."""
	frappe.only_for("System Manager")
	if not frappe.has_permission(SETTING_DOCTYPE, "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	doc = frappe.get_doc(SETTING_DOCTYPE)
	if not doc.enable_shopify or doc.auth_method != AUTH_METHOD_OAUTH:
		frappe.throw(_("Set Authentication method to OAuth and enable Shopify first."))
	if not (doc.shopify_url and doc.client_id and doc.shared_secret):
		frappe.throw(_("Enter Shop URL, Client ID, and API Secret before connecting."))

	shop = doc.shopify_url.replace("https://", "").strip("/")
	redirect_uri = get_url("/api/method/ecommerce_integrations.shopify.oauth.shopify_oauth_callback")

	state = secrets.token_urlsafe(32)
	frappe.cache().set_value(
		f"{OAUTH_STATE_CACHE_PREFIX}{state}",
		{"shop": shop},
		expires_in_sec=OAUTH_STATE_TTL_SEC,
	)

	params = {
		"client_id": doc.client_id.strip(),
		"scope": SHOPIFY_OAUTH_SCOPES,
		"redirect_uri": redirect_uri,
		"state": state,
	}
	return f"https://{shop}/admin/oauth/authorize?{urlencode(params)}"


@frappe.whitelist(allow_guest=True, methods=["GET"])
def shopify_oauth_callback() -> None:
	"""Shopify redirects here after merchant approves the app."""
	args = frappe.request.args
	code = args.get("code")
	state = args.get("state")
	shop = args.get("shop")

	if not code or not state or not shop:
		_redirect_to_form(shopify_oauth="error", shopify_oauth_message=str(_("Missing OAuth parameters")))
		return

	cache_key = f"{OAUTH_STATE_CACHE_PREFIX}{state}"
	payload = frappe.cache().get_value(cache_key)
	if not payload:
		_redirect_to_form(shopify_oauth="error", shopify_oauth_message=str(_("Invalid or expired session. Start again.")))
		return

	frappe.cache().delete_value(cache_key)

	expected_shop = (payload.get("shop") or "").replace("https://", "").strip("/")
	normalized_shop = shop.replace("https://", "").strip("/")
	if expected_shop != normalized_shop:
		_redirect_to_form(shopify_oauth="error", shopify_oauth_message=str(_("Shop does not match OAuth session.")))
		return

	try:
		_exchange_and_persist_token(code=code, shop=normalized_shop)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Shopify OAuth callback")
		_redirect_to_form(
			shopify_oauth="error",
			shopify_oauth_message=str(_("Token exchange failed. Check API secret and try again.")),
		)
		return

	_redirect_to_form(shopify_oauth="success")


def _exchange_and_persist_token(*, code: str, shop: str) -> None:
	doc = frappe.get_doc(SETTING_DOCTYPE)
	client_id = (doc.client_id or "").strip()
	client_secret = doc.shared_secret
	if not client_id or not client_secret:
		frappe.throw(_("Client ID and API Secret are required on Shopify Setting."))

	url = f"https://{shop}/admin/oauth/access_token"
	resp = requests.post(
		url,
		json={"client_id": client_id, "client_secret": client_secret, "code": code},
		timeout=30,
	)
	if resp.status_code == 401:
		connection.mark_shopify_connection_needs_reconnection(
			_("Shopify rejected the OAuth token exchange (401). Verify API secret and Client ID.")
		)
	resp.raise_for_status()
	body = resp.json()
	access_token = body.get("access_token")
	if not access_token:
		frappe.throw(_("Shopify response did not include an access token."))

	frappe.set_user("Administrator")
	try:
		password.set_encrypted_password(SETTING_DOCTYPE, SETTING_DOCTYPE, access_token, fieldname="password")
		frappe.db.set_single_value(
			SETTING_DOCTYPE,
			{
				"auth_method": AUTH_METHOD_OAUTH,
				"shopify_connection_status": CONNECTION_STATUS_CONNECTED,
			},
			update_modified=False,
		)

		doc = frappe.get_doc(SETTING_DOCTYPE)
		if not doc.webhooks:
			new_webhooks = connection.register_webhooks(doc.shopify_url, access_token)
			for wh in new_webhooks:
				doc.append("webhooks", {"webhook_id": wh.id, "method": wh.topic})
			doc.flags.ignore_permissions = True
			doc.flags.shopify_webhooks_registered_now = True
			doc.save()
	finally:
		frappe.set_user("Guest")
