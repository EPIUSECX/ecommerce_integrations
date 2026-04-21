import base64
import functools
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import frappe
import requests
from frappe import _
from frappe.utils import password
from frappe.utils import cstr
from frappe.exceptions import DuplicateEntryError, UniqueValidationError
from shopify.resources import Webhook
from shopify.session import Session

from ecommerce_integrations.shopify.constants import (
	API_VERSION,
	AUTH_METHOD_CLIENT_CREDENTIALS,
	CONNECTION_STATUS_NEEDS_RECONNECTION,
	EVENT_MAPPER,
	SETTING_DOCTYPE,
	WEBHOOK_EVENTS,
)
from ecommerce_integrations.shopify.utils import create_shopify_log

CLIENT_CREDENTIALS_CACHE_PREFIX = "shopify_client_credentials_token:"


def get_shopify_access_token(setting=None, *, allow_refresh: bool = True):
	"""Return the Admin API access token for Shopify, or None if missing."""
	doc = setting or frappe.get_doc(SETTING_DOCTYPE)
	if not doc.is_enabled():
		return None

	if doc.auth_method == AUTH_METHOD_CLIENT_CREDENTIALS:
		return _get_client_credentials_access_token(doc, allow_refresh=allow_refresh)

	token = doc.get_password("password")
	return token or None


def _get_client_credentials_access_token(setting, *, allow_refresh: bool = True) -> str | None:
	cached_token = _get_cached_client_credentials_token()
	if cached_token:
		return cached_token

	stored_token = setting.get_password("password")
	if stored_token and not allow_refresh:
		return stored_token

	if not allow_refresh:
		return None

	client_id = (setting.client_id or "").strip()
	client_secret = setting.shared_secret
	if not client_id or not client_secret or not setting.shopify_url:
		return None

	token, expires_in = _request_client_credentials_token(
		shop=setting.shopify_url,
		client_id=client_id,
		client_secret=client_secret,
	)
	_store_client_credentials_token(token=token, expires_in=expires_in)
	password.set_encrypted_password(SETTING_DOCTYPE, SETTING_DOCTYPE, token, fieldname="password")
	return token


def _get_cached_client_credentials_token() -> str | None:
	payload = frappe.cache().get_value(_get_client_credentials_cache_key())
	if not payload:
		return None

	token = payload.get("token")
	expires_at = payload.get("expires_at")
	if not token or not expires_at:
		return None

	try:
		expiry = datetime.fromisoformat(expires_at)
	except ValueError:
		return None

	if expiry <= datetime.now(timezone.utc):
		frappe.cache().delete_value(_get_client_credentials_cache_key())
		return None

	return token


def _store_client_credentials_token(*, token: str, expires_in: int | None) -> None:
	ttl = max(int(expires_in or 86400) - 300, 60)
	expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl)
	frappe.cache().set_value(
		_get_client_credentials_cache_key(),
		{"token": token, "expires_at": expiry.isoformat()},
		expires_in_sec=ttl,
	)


def _get_client_credentials_cache_key() -> str:
	return f"{CLIENT_CREDENTIALS_CACHE_PREFIX}{frappe.local.site}"


def _request_client_credentials_token(*, shop: str, client_id: str, client_secret: str) -> tuple[str, int | None]:
	url = f"https://{shop}/admin/oauth/access_token"
	resp = requests.post(
		url,
		data={
			"grant_type": "client_credentials",
			"client_id": client_id,
			"client_secret": client_secret,
		},
		headers={"Content-Type": "application/x-www-form-urlencoded"},
		timeout=30,
	)
	if resp.status_code == 401:
		mark_shopify_connection_needs_reconnection(
			_("Shopify rejected the client credentials token request (401). Verify Client ID and API secret.")
		)
	resp.raise_for_status()
	body = resp.json()
	access_token = body.get("access_token")
	if not access_token:
		frappe.throw(_("Shopify response did not include an access token."))
	return access_token, body.get("expires_in")


def mark_shopify_connection_needs_reconnection(message: str | None = None) -> None:
	"""Mark integration as needing re-auth after Shopify returns 401 / unauthorized."""
	if not frappe.db.exists(SETTING_DOCTYPE, SETTING_DOCTYPE):
		return
	frappe.db.set_single_value(
		SETTING_DOCTYPE,
		"shopify_connection_status",
		CONNECTION_STATUS_NEEDS_RECONNECTION,
		update_modified=False,
	)
	if message:
		create_shopify_log(status="Error", message=message)


def handle_shopify_api_auth_error(exc: BaseException) -> None:
	"""If exception indicates Shopify auth failure, update connection status (no secrets in logs)."""
	if _is_shopify_unauthorized_error(exc):
		frappe.cache().delete_value(_get_client_credentials_cache_key())
		mark_shopify_connection_needs_reconnection(
			_("Shopify returned an authentication error. Reconnect or update the access token in Shopify Setting.")
		)


def _is_shopify_unauthorized_error(exc: BaseException) -> bool:
	try:
		from pyactiveresource.connection import UnauthorizedAccess

		if isinstance(exc, UnauthorizedAccess):
			return True
	except Exception:
		frappe.clear_last_message()

	resp = getattr(exc, "response", None)
	code = getattr(resp, "status_code", None) if resp is not None else None
	if code == 401:
		return True
	return "401" in cstr(exc)


def temp_shopify_session(func):
	"""Any function that needs to access shopify api needs this decorator. The decorator starts a temp session that's destroyed when function returns."""

	@functools.wraps(func)
	def wrapper(*args, **kwargs):
		# no auth in testing
		if frappe.flags.in_test:
			return func(*args, **kwargs)

		setting = frappe.get_doc(SETTING_DOCTYPE)
		if setting.is_enabled():
			token = get_shopify_access_token(setting)
			if not token:
				frappe.throw(
					_("Configure Shopify authentication (OAuth, Client Credentials, or access token) before using this action.")
				)
			auth_details = (setting.shopify_url, API_VERSION, token)
			try:
				with Session.temp(*auth_details):
					return func(*args, **kwargs)
			except Exception as e:
				handle_shopify_api_auth_error(e)
				raise

	return wrapper


@frappe.whitelist()
def test_shopify_connection() -> dict:
	frappe.only_for("System Manager")
	doc = frappe.get_doc(SETTING_DOCTYPE)
	if not doc.is_enabled():
		frappe.throw(_("Enable Shopify before testing the connection."))

	token = get_shopify_access_token(doc)
	if not token:
		frappe.throw(_("Unable to obtain a Shopify access token with the current settings."))

	resp = requests.get(
		f"https://{doc.shopify_url}/admin/api/{API_VERSION}/shop.json",
		headers={"X-Shopify-Access-Token": token},
		timeout=30,
	)
	if resp.status_code == 401:
		mark_shopify_connection_needs_reconnection(
			_("Shopify rejected the connection test (401). Verify the configured credentials.")
		)
	resp.raise_for_status()
	body = resp.json() or {}
	shop_name = ((body.get("shop") or {}).get("name")) or doc.shopify_url

	if not doc.webhooks:
		new_webhooks = register_webhooks(doc.shopify_url, token)
		for wh in new_webhooks:
			doc.append("webhooks", {"webhook_id": wh.id, "method": wh.topic})
		doc.flags.ignore_permissions = True
		doc.flags.shopify_webhooks_registered_now = True
		doc.save()

	frappe.db.set_single_value(
		SETTING_DOCTYPE,
		"shopify_connection_status",
		"Connected",
		update_modified=False,
	)
	return {"shop": shop_name}


def register_webhooks(shopify_url: str, password: str) -> list[Webhook]:
	"""Register required webhooks with shopify and return registered webhooks."""
	new_webhooks = []

	# clear all stale webhooks matching current site url before registering new ones
	unregister_webhooks(shopify_url, password)

	with Session.temp(shopify_url, API_VERSION, password):
		for topic in WEBHOOK_EVENTS:
			webhook = Webhook.create({"topic": topic, "address": get_callback_url(), "format": "json"})

			if webhook.is_valid():
				new_webhooks.append(webhook)
			else:
				create_shopify_log(
					status="Error",
					response_data=webhook.to_dict(),
					exception=webhook.errors.full_messages(),
				)

	return new_webhooks


def unregister_webhooks(shopify_url: str, password: str) -> None:
	"""Unregister all webhooks from shopify that correspond to current site url."""
	url = get_current_domain_name()

	with Session.temp(shopify_url, API_VERSION, password):
		for webhook in Webhook.find():
			if url in webhook.address:
				webhook.destroy()


def get_current_domain_name() -> str:
	"""Get current site domain name. E.g. test.erpnext.com

	If developer_mode is enabled and localtunnel_url is set in site config then domain  is set to localtunnel_url.
	"""
	if frappe.conf.developer_mode and frappe.conf.localtunnel_url:
		return frappe.conf.localtunnel_url
	else:
		return frappe.request.host


def get_callback_url() -> str:
	"""Shopify calls this url when new events occur to subscribed webhooks.

	If developer_mode is enabled and localtunnel_url is set in site config then callback url is set to localtunnel_url.
	"""
	url = get_current_domain_name()

	return f"https://{url}/api/method/ecommerce_integrations.shopify.connection.store_request_data"


@frappe.whitelist(allow_guest=True)
def store_request_data() -> None:
	if frappe.request:
		hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")

		_validate_request(frappe.request, hmac_header)

		data = json.loads(frappe.request.data)
		event = frappe.request.headers.get("X-Shopify-Topic")
		delivery_id = frappe.get_request_header("X-Shopify-Webhook-Id")

		if not frappe.flags.in_test and delivery_id and not try_reserve_webhook_receipt(delivery_id, event):
			return

		process_request(data, event)


def try_reserve_webhook_receipt(delivery_id: str, topic: str | None) -> bool:
	"""Record webhook delivery id. Return False if this delivery was already processed (idempotent retry)."""
	try:
		frappe.get_doc(
			{
				"doctype": "Shopify Webhook Receipt",
				"delivery_id": delivery_id,
				"topic": topic or "",
			}
		).insert(ignore_permissions=True)
	except (DuplicateEntryError, UniqueValidationError):
		return False
	return True


def process_request(data, event):
	handler = EVENT_MAPPER.get(event)
	if not handler:
		create_shopify_log(
			status="Invalid",
			request_data=data,
			message=_("Unknown or unsupported Shopify webhook topic: {0}").format(event or ""),
		)
		return

	# create log
	log = create_shopify_log(method=handler, request_data=data)

	# enqueue backround job
	frappe.enqueue(
		method=handler,
		queue="short",
		timeout=300,
		is_async=True,
		**{"payload": data, "request_id": log.name},
	)


def _validate_request(req, hmac_header):
	settings = frappe.get_doc(SETTING_DOCTYPE)
	secret_key = settings.shared_secret

	sig = base64.b64encode(hmac.new(secret_key.encode("utf8"), req.data, hashlib.sha256).digest())

	if sig != bytes(hmac_header.encode()):
		create_shopify_log(status="Error", request_data=req.data)
		frappe.throw(_("Unverified Webhook Data"))
