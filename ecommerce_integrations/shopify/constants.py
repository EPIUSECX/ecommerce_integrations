# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE


MODULE_NAME = "shopify"
SETTING_DOCTYPE = "Shopify Setting"
OLD_SETTINGS_DOCTYPE = "Shopify Settings"

API_VERSION = "2026-04"

WEBHOOK_EVENTS = [
	"products/create",
	"orders/create",
	"orders/updated",
	"orders/paid",
	"orders/fulfilled",
	"orders/cancelled",
	"orders/partially_fulfilled",
	"refunds/create",
]

EVENT_MAPPER = {
	"products/create": "ecommerce_integrations.shopify.product.sync_product_from_shopify",
	"orders/create": "ecommerce_integrations.shopify.order.sync_sales_order",
	"orders/updated": "ecommerce_integrations.shopify.order.sync_sales_order_updated",
	"orders/paid": "ecommerce_integrations.shopify.invoice.prepare_sales_invoice",
	"orders/fulfilled": "ecommerce_integrations.shopify.fulfillment.prepare_delivery_note",
	"orders/cancelled": "ecommerce_integrations.shopify.order.cancel_order",
	"orders/partially_fulfilled": "ecommerce_integrations.shopify.fulfillment.prepare_delivery_note",
	"refunds/create": "ecommerce_integrations.shopify.refund.sync_refund",
}

SHOPIFY_VARIANTS_ATTR_LIST = ["option1", "option2", "option3"]

# custom fields

CUSTOMER_ID_FIELD = "shopify_customer_id"
ORDER_ID_FIELD = "shopify_order_id"
ORDER_NUMBER_FIELD = "shopify_order_number"
ORDER_STATUS_FIELD = "shopify_order_status"
FULLFILLMENT_ID_FIELD = "shopify_fulfillment_id"
SUPPLIER_ID_FIELD = "shopify_supplier_id"
ADDRESS_ID_FIELD = "shopify_address_id"
ORDER_ITEM_DISCOUNT_FIELD = "shopify_item_discount"
SHOPIFY_LINE_ITEM_ID_FIELD = "shopify_line_item_id"
SHOPIFY_REFUND_ID_FIELD = "shopify_refund_id"
ITEM_SELLING_RATE_FIELD = "shopify_selling_rate"

# ERPNext already defines the default UOMs from Shopify but names are different
WEIGHT_TO_ERPNEXT_UOM_MAP = {"kg": "Kg", "g": "Gram", "oz": "Ounce", "lb": "Pound"}

# Comma-separated Admin API scopes for OAuth authorize URL (align with Partner app / shopify.app.toml).
SHOPIFY_OAUTH_SCOPES = (
	"read_orders,write_orders,read_customers,write_customers,read_products,write_products,"
	"read_inventory,write_inventory,read_locations,read_fulfillments,write_fulfillments"
)

AUTH_METHOD_MANUAL = "Manual"
AUTH_METHOD_CLIENT_CREDENTIALS = "Client Credentials"
AUTH_METHOD_OAUTH = "OAuth"

CONNECTION_STATUS_NOT_CONNECTED = "Not Connected"
CONNECTION_STATUS_CONNECTED = "Connected"
CONNECTION_STATUS_NEEDS_RECONNECTION = "Needs Reconnection"

EXPORT_PRODUCTS_JOB_NAME = "shopify.job.export.all.products"
EXPORT_PRODUCTS_REALTIME_KEY = "shopify.key.export.all.products"
