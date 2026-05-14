# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import json

import frappe
from frappe.tests import IntegrationTestCase

from ecommerce_integrations.shopify.refund import _remove_shipping_taxes


class ReturnDocument:
	def __init__(self, taxes):
		self.taxes = taxes

	def get(self, fieldname):
		return getattr(self, fieldname)

	def remove(self, row):
		self.taxes.remove(row)


class TestRefund(IntegrationTestCase):
	def test_remove_shipping_taxes_keeps_sales_tax_without_item_detail(self):
		credit_note = ReturnDocument(
			[
				frappe._dict(
					charge_type="Actual",
					account_head="VAT - CO",
					tax_amount=-451.68,
					item_wise_tax_detail=None,
				),
				frappe._dict(
					charge_type="Actual",
					account_head="Shipping - CO",
					tax_amount=-100,
					item_wise_tax_detail=None,
				),
			]
		)
		setting = frappe._dict(
			shipping_item="Shipping Item",
			default_shipping_charges_account="Shipping - CO",
		)

		_remove_shipping_taxes(credit_note, setting)

		self.assertEqual(len(credit_note.taxes), 1)
		self.assertEqual(credit_note.taxes[0].account_head, "VAT - CO")

	def test_remove_shipping_taxes_only_removes_shipping_item_tax_detail(self):
		credit_note = ReturnDocument(
			[
				frappe._dict(
					charge_type="Actual",
					account_head="VAT - CO",
					tax_amount=-60,
					item_wise_tax_detail=json.dumps(
						{
							"Product": [15, -45],
							"Shipping Item": [15, -15],
						}
					),
				)
			]
		)
		setting = frappe._dict(
			shipping_item="Shipping Item",
			default_shipping_charges_account="Shipping - CO",
		)

		_remove_shipping_taxes(credit_note, setting)

		self.assertEqual(len(credit_note.taxes), 1)
		self.assertEqual(credit_note.taxes[0].tax_amount, -45)
		self.assertEqual(
			json.loads(credit_note.taxes[0].item_wise_tax_detail),
			{"Product": [15, -45]},
		)
