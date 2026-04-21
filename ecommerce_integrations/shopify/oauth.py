# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

"""Shopify OAuth (authorization code) install for Shopify Setting.

See Shopify: https://shopify.dev/docs/apps/auth/oauth/getting-started
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit

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
SHOP_DOMAIN_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com$")


def _oauth_redirect(url: str) -> None:
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = url


def _redirect_to_form(**query_params) -> None:
	target = get_url_to_form(SETTING_DOCTYPE, SETTING_DOCTYPE)
	if query_params:
		sep = "&" if "?" in target else "?"
		target = target + sep + urlencode(query_params)
	_oauth_redirect(target)


def _get_shopify_redirect_uri() -> str:
	"""Build a public callback URL without any internal bench port."""
	redirect_uri = get_url("/api/method/ecommerce_integrations.shopify.oauth.shopify_oauth_callback")
	parsed = urlsplit(redirect_uri)
	if parsed.port and parsed.hostname:
		redirect_uri = urlunsplit((parsed.scheme, parsed.hostname, parsed.path, parsed.query, parsed.fragment))
	return redirect_uri


def _normalize_shop_domain(shop: str) -> str:
	normalized = (shop or "").replace("https://", "").replace("http://", "").strip().strip("/").lower()
	if not SHOP_DOMAIN_PATTERN.fullmatch(normalized):
		frappe.throw(_("Enter a valid Shopify Shop URL ending in .myshopify.com."))
	return normalized


def _build_hmac_message(params) -> str:
	items = []
	for key in sorted(params):
		if key in {"hmac", "signature"}:
			continue
		value = params.get(key)
		if value is None:
			continue
		items.append(f"{key}={value}")
	return "&".join(items)


def _is_valid_oauth_callback(args, shared_secret: str) -> bool:
	received_hmac = (args.get("hmac") or "").strip()
	if not received_hmac or not shared_secret:
		return False

	message = _build_hmac_message(args)
	computed_hmac = hmac.new(shared_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
	return hmac.compare_digest(computed_hmac, received_hmac)


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

	shop = _normalize_shop_domain(doc.shopify_url)
	redirect_uri = _get_shopify_redirect_uri()

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

	doc = frappe.get_doc(SETTING_DOCTYPE)
	if not _is_valid_oauth_callback(args, doc.shared_secret):
		_redirect_to_form(shopify_oauth="error", shopify_oauth_message=str(_("Shopify callback verification failed.")))
		return

	cache_key = f"{OAUTH_STATE_CACHE_PREFIX}{state}"
	payload = frappe.cache().get_value(cache_key)
	if not payload:
		_redirect_to_form(shopify_oauth="error", shopify_oauth_message=str(_("Invalid or expired session. Start again.")))
		return

	frappe.cache().delete_value(cache_key)

	expected_shop = _normalize_shop_domain(payload.get("shop") or "")
	normalized_shop = _normalize_shop_domain(shop)
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
		connection._persist_shopify_access_token(access_token, auth_method=AUTH_METHOD_OAUTH)

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
