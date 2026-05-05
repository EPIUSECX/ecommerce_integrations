# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ecommerce_integrations.shopify.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	FULLFILLMENT_ID_FIELD,
	ITEM_SELLING_RATE_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	WEBHOOK_EVENTS,
	SUPPLIER_ID_FIELD,
)
from ecommerce_integrations.shopify.utils import _migrate_items_to_ecommerce_item

from .shopify_setting import setup_custom_fields


class TestShopifySetting(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		frappe.db.sql(
			"""delete from `tabCustom Field`
			where name like '%shopify%'"""
		)

	def test_custom_field_creation(self):
		setup_custom_fields()

		created_fields = frappe.get_all(
			"Custom Field",
			filters={"fieldname": ["LIKE", "%shopify%"]},
			fields="fieldName",
			as_list=True,
			order_by=None,
		)

		required_fields = set(
			[
				ADDRESS_ID_FIELD,
				CUSTOMER_ID_FIELD,
				FULLFILLMENT_ID_FIELD,
				ITEM_SELLING_RATE_FIELD,
				ORDER_ID_FIELD,
				ORDER_NUMBER_FIELD,
				ORDER_STATUS_FIELD,
				SUPPLIER_ID_FIELD,
				ORDER_ITEM_DISCOUNT_FIELD,
			]
		)

		self.assertGreaterEqual(len(created_fields), 13)
		created_fields_set = {d[0] for d in created_fields}

		self.assertEqual(created_fields_set, required_fields)

	def test_handle_webhooks_reregisters_when_topics_are_missing(self):
		setting = frappe.get_doc("Shopify Setting")
		setting.update(
			{
				"enable_shopify": 1,
				"shopify_url": "frappetest.myshopify.com",
				"shared_secret": "supersecret",
				"auth_method": "Manual",
			}
		)
		setting.set_password("password", "supersecret")
		setting.webhooks = [{"webhook_id": "1", "method": "orders/create"}]

		with patch(
			"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.get_shopify_access_token",
			return_value="supersecret",
		), patch(
			"ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting.connection.register_webhooks",
			return_value=[
				type("Webhook", (), {"id": str(idx), "topic": topic})()
				for idx, topic in enumerate(WEBHOOK_EVENTS, start=1)
			],
		):
			setting._handle_webhooks()

		self.assertEqual({row.method for row in setting.webhooks}, set(WEBHOOK_EVENTS))

	def test_old_connector_migration_completes_when_old_fields_are_missing(self):
		frappe.db.delete("Custom Field", {"fieldname": ("in", ["shopify_product_id", "shopify_variant_id"])})
		frappe.db.set_value("Shopify Setting", "Shopify Setting", "is_old_data_migrated", 0)
		log = frappe.get_doc(
			{
				"doctype": "Ecommerce Integration Log",
				"integration": "shopify",
				"status": "Queued",
				"method": "ecommerce_integrations.shopify.utils.migrate_from_old_connector",
			}
		).insert(ignore_permissions=True)

		_migrate_items_to_ecommerce_item(log)

		log.reload()
		self.assertEqual(log.status, "Success")
		self.assertEqual(
			frappe.db.get_value("Shopify Setting", "Shopify Setting", "is_old_data_migrated"),
			1,
		)
