# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import hashlib
import hmac
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import requests

from ecommerce_integrations.shopify import connection
from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE


class TestShopifyOAuthHelpers(unittest.TestCase):
	def test_is_shopify_unauthorized_detects_http_401(self):
		resp = requests.Response()
		resp.status_code = 401
		err = requests.HTTPError(response=resp)
		self.assertTrue(connection._is_shopify_unauthorized_error(err))

	def test_is_shopify_unauthorized_false_for_500(self):
		resp = requests.Response()
		resp.status_code = 500
		err = requests.HTTPError(response=resp)
		self.assertFalse(connection._is_shopify_unauthorized_error(err))

	def test_get_shopify_redirect_uri_strips_internal_port(self):
		from ecommerce_integrations.shopify.oauth import _get_shopify_redirect_uri

		with patch("ecommerce_integrations.shopify.oauth.get_url") as mocked_get_url:
			mocked_get_url.return_value = "https://cutover.nbg.frappe.cloud:8000/api/method/ecommerce_integrations.shopify.oauth.shopify_oauth_callback"

			redirect_uri = _get_shopify_redirect_uri()

		self.assertEqual(
			redirect_uri,
			"https://cutover.nbg.frappe.cloud/api/method/ecommerce_integrations.shopify.oauth.shopify_oauth_callback",
		)

	def test_normalize_shop_domain_requires_myshopify_domain(self):
		from ecommerce_integrations.shopify.oauth import _normalize_shop_domain

		self.assertEqual(_normalize_shop_domain("https://Example-Store.myshopify.com/"), "example-store.myshopify.com")
		with self.assertRaises(frappe.ValidationError):
			_normalize_shop_domain("https://admin.shopify.com/store/example")

	def test_valid_oauth_callback_hmac(self):
		from ecommerce_integrations.shopify.oauth import _is_valid_oauth_callback

		args = {
			"code": "sample-code",
			"shop": "example-store.myshopify.com",
			"state": "sample-state",
			"timestamp": "1710000000",
		}
		message = "&".join(f"{key}={args[key]}" for key in sorted(args))
		secret = "shpss_example"
		args["hmac"] = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

		self.assertTrue(_is_valid_oauth_callback(args, secret))


class TestShopifyClientCredentials(unittest.TestCase):
	def test_test_shopify_connection_reports_unauthorized_as_validation_error(self):
		doc = SimpleNamespace(
			is_enabled=lambda: True,
			auth_method="Manual",
			shopify_url="example.myshopify.com",
			webhooks=[SimpleNamespace(webhook_id="1", method="orders/create")],
		)
		resp = requests.Response()
		resp.status_code = 401

		def throw_validation_error(message):
			raise frappe.ValidationError(message)

		with (
			patch("ecommerce_integrations.shopify.connection.frappe.only_for"),
			patch("ecommerce_integrations.shopify.connection.frappe.get_doc", return_value=doc),
			patch("ecommerce_integrations.shopify.connection.frappe.throw", side_effect=throw_validation_error),
			patch("ecommerce_integrations.shopify.connection.get_shopify_access_token", return_value="bad-token"),
			patch("ecommerce_integrations.shopify.connection._persist_shopify_access_token"),
			patch("ecommerce_integrations.shopify.connection.requests.get", return_value=resp),
			patch("ecommerce_integrations.shopify.connection.mark_shopify_connection_needs_reconnection") as mocked_mark,
			patch("ecommerce_integrations.shopify.connection._", side_effect=lambda msg, *args, **kwargs: msg),
		):
			with self.assertRaises(frappe.ValidationError):
				connection.test_shopify_connection()

		mocked_mark.assert_called_once()

	def test_get_shopify_access_token_uses_cached_client_credentials_token(self):
		cache = {}

		def get_value(key):
			return cache.get(key)

		def set_value(key, value, expires_in_sec=None):
			cache[key] = value

		doc = SimpleNamespace(
			is_enabled=lambda: True,
			auth_method="Client Credentials",
			client_id="client-id",
			shared_secret="shpss_secret",
			shopify_url="example.myshopify.com",
			get_password=lambda fieldname: None,
		)
		payload = {"token": "cached-token", "expires_at": "2999-01-01T00:00:00+00:00"}
		cache["shopify_client_credentials_token:test-site"] = payload

		with (
			patch("frappe.local", SimpleNamespace(site="test-site")),
			patch("ecommerce_integrations.shopify.connection.frappe.cache", return_value=SimpleNamespace(get_value=get_value)),
			patch("ecommerce_integrations.shopify.connection._persist_shopify_access_token"),
		):
			from ecommerce_integrations.shopify.connection import get_shopify_access_token

			token = get_shopify_access_token(doc)

		self.assertEqual(token, "cached-token")

	def test_get_shopify_access_token_does_not_reuse_persisted_client_credentials_token_without_cache(self):
		doc = SimpleNamespace(
			is_enabled=lambda: True,
			auth_method="Client Credentials",
			client_id="client-id",
			shared_secret="shpss_secret",
			shopify_url="example.myshopify.com",
			get_password=lambda fieldname: "persisted-but-stale-token",
		)

		with (
			patch("frappe.local", SimpleNamespace(site="test-site")),
			patch(
				"ecommerce_integrations.shopify.connection.frappe.cache",
				return_value=SimpleNamespace(get_value=lambda key: None),
			),
		):
			token = connection.get_shopify_access_token(doc, allow_refresh=False)

		self.assertIsNone(token)

	def test_client_credentials_webhook_setup_refreshes_token_on_save(self):
		from ecommerce_integrations.shopify.constants import AUTH_METHOD_CLIENT_CREDENTIALS
		from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import ShopifySetting

		doc = ShopifySetting.__new__(ShopifySetting)
		doc.auth_method = AUTH_METHOD_CLIENT_CREDENTIALS
		doc.enable_shopify = 1
		doc.webhooks = []
		doc.shopify_url = "example.myshopify.com"
		doc.flags = SimpleNamespace()
		doc.append = lambda *args, **kwargs: None
		doc.is_enabled = lambda: True

		webhook = SimpleNamespace(id=1, topic="orders/create")

		with (
			patch(
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.get_shopify_access_token",
				return_value="fresh-token",
			) as mocked_get_token,
			patch(
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.register_webhooks",
				return_value=[webhook],
			),
		):
			doc._handle_webhooks()

		mocked_get_token.assert_called_once_with(doc, allow_refresh=True)
		self.assertTrue(doc.flags.shopify_webhooks_registered_now)
		self.assertEqual(doc.shopify_connection_status, "Connected")

	def test_client_credentials_webhook_setup_marks_reconnection_on_unauthorized(self):
		from ecommerce_integrations.shopify.constants import AUTH_METHOD_CLIENT_CREDENTIALS
		from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import ShopifySetting

		doc = ShopifySetting.__new__(ShopifySetting)
		doc.auth_method = AUTH_METHOD_CLIENT_CREDENTIALS
		doc.enable_shopify = 1
		doc.webhooks = []
		doc.shopify_url = "example.myshopify.com"
		doc.flags = SimpleNamespace()
		doc.append = lambda *args, **kwargs: None
		doc.is_enabled = lambda: True

		resp = requests.Response()
		resp.status_code = 401
		err = requests.HTTPError(response=resp)

		with (
			patch(
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.get_shopify_access_token",
				return_value="stale-token",
			),
			patch(
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.register_webhooks",
				side_effect=err,
			),
			patch(
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.handle_shopify_api_auth_error"
			) as mocked_handle_error,
		):
			doc._handle_webhooks()

		mocked_handle_error.assert_called_once_with(err)
		self.assertEqual(doc.shopify_connection_status, "Needs Reconnection")
		self.assertFalse(getattr(doc.flags, "shopify_webhooks_registered_now", False))

	def test_client_credentials_webhook_setup_retries_with_fresh_token_after_unauthorized(self):
		from ecommerce_integrations.shopify.constants import AUTH_METHOD_CLIENT_CREDENTIALS
		from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import ShopifySetting

		doc = ShopifySetting.__new__(ShopifySetting)
		doc.auth_method = AUTH_METHOD_CLIENT_CREDENTIALS
		doc.enable_shopify = 1
		doc.webhooks = []
		doc.shopify_url = "example.myshopify.com"
		doc.flags = SimpleNamespace()
		doc.append = lambda *args, **kwargs: None
		doc.is_enabled = lambda: True

		resp = requests.Response()
		resp.status_code = 401
		err = requests.HTTPError(response=resp)
		webhook = SimpleNamespace(id=1, topic="orders/create")

		with (
			patch(
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.get_shopify_access_token",
				side_effect=["stale-token", "fresh-token"],
			) as mocked_get_token,
			patch(
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.register_webhooks",
				side_effect=[err, [webhook]],
			) as mocked_register,
			patch(
				"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.handle_shopify_api_auth_error"
			) as mocked_handle_error,
		):
			doc._handle_webhooks()

		self.assertEqual(mocked_get_token.call_args_list[0].kwargs, {"allow_refresh": True})
		self.assertEqual(mocked_get_token.call_args_list[1].kwargs, {"allow_refresh": True})
		self.assertEqual(mocked_register.call_args_list[0].args, ("example.myshopify.com", "stale-token"))
		self.assertEqual(mocked_register.call_args_list[1].args, ("example.myshopify.com", "fresh-token"))
		mocked_handle_error.assert_called_once_with(err)
		self.assertTrue(doc.flags.shopify_webhooks_registered_now)
		self.assertEqual(doc.shopify_connection_status, "Connected")

	def test_request_client_credentials_token(self):
		from ecommerce_integrations.shopify.connection import _request_client_credentials_token

		class _Resp:
			status_code = 200

			def raise_for_status(self):
				return None

			def json(self):
				return {"access_token": "fresh-token", "expires_in": 86400}

		with patch("ecommerce_integrations.shopify.connection.requests.post", return_value=_Resp()) as mocked_post:
			token, expires_in = _request_client_credentials_token(
				shop="example.myshopify.com",
				client_id="client-id",
				client_secret="shpss_secret",
			)

		self.assertEqual(token, "fresh-token")
		self.assertEqual(expires_in, 86400)
		mocked_post.assert_called_once()


class TestShopifyOAuthIntegration(unittest.TestCase):
	def test_get_shopify_access_token_when_disabled(self):
		doc = frappe.get_doc(SETTING_DOCTYPE)
		prev = doc.enable_shopify
		doc.enable_shopify = 0
		try:
			self.assertIsNone(connection.get_shopify_access_token(doc))
		finally:
			doc.enable_shopify = prev

	def test_shopify_oauth_start_requires_oauth_mode(self):
		if not frappe.get_meta(SETTING_DOCTYPE).has_field("auth_method"):
			self.skipTest("Shopify Setting schema missing auth_method; run bench migrate.")
		frappe.set_user("Administrator")
		prev_enable = frappe.db.get_single_value(SETTING_DOCTYPE, "enable_shopify")
		prev_auth = frappe.db.get_single_value(SETTING_DOCTYPE, "auth_method") or "Manual"
		try:
			frappe.db.set_single_value(SETTING_DOCTYPE, "enable_shopify", 1)
			frappe.db.set_single_value(SETTING_DOCTYPE, "auth_method", "Manual")
			frappe.clear_cache()
			from ecommerce_integrations.shopify.oauth import shopify_oauth_start

			with self.assertRaises(frappe.ValidationError):
				shopify_oauth_start()
		finally:
			frappe.db.set_single_value(SETTING_DOCTYPE, "enable_shopify", prev_enable)
			frappe.db.set_single_value(SETTING_DOCTYPE, "auth_method", prev_auth)
			frappe.clear_cache()

	def test_shopify_oauth_callback_rejects_invalid_state(self):
		from ecommerce_integrations.shopify.oauth import shopify_oauth_callback

		class _Headers:
			def get(self, key, default=""):
				return default

		class _Req:
			args = {"code": "abc", "state": "missing-from-cache", "shop": "test-shop.myshopify.com"}
			host = "localhost"
			headers = _Headers()

		frappe.set_user("Guest")
		frappe.local.request = _Req()
		frappe.request = frappe.local.request
		try:
			shopify_oauth_callback()
			self.assertEqual(frappe.local.response.get("type"), "redirect")
			self.assertIn("shopify_oauth=error", frappe.local.response.get("location", ""))
		finally:
			frappe.local.request = None
			frappe.request = None
