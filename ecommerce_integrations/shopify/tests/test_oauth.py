# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import unittest

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
