# Copyright (c) 2013, Rohit Industries Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from datetime import date
from rohit_common.rohit_common.report.rigpl_legacy_gstr1.rigpl_legacy_gstr1 import Gstr1Report

def execute(filters=None):
	return ClearTaxImport(filters).run()

class ClearTaxImport(Gstr1Report):
	def __init__(self, filters=None):
		self.filters = frappe._dict(filters or {})
		self.columns = []
		self.data = []
		self.doctype = filters.get("type")
		if filters.get("type") == 'Sales Invoice':
			self.tax_doctype = "Sales Taxes and Charges"
			self.select_columns = """
				name as invoice_number,
				customer,
				posting_date as posting_date_unformatted,
				base_grand_total,
				base_net_total,
				taxes_and_charges,

				COALESCE(NULLIF(customer_gstin,''), NULLIF(billing_address_gstin, '')) as customer_gstin,
				place_of_supply,
				ecommerce_gstin,
				reverse_charge,
				invoice_type,
				return_against,
				is_return,
				invoice_type,
				export_type,
				port_code,
				shipping_bill_number,
				shipping_bill_date,
				reason_for_issuing_document
			"""
		elif filters.get("type") == 'Purchase Invoice':
			self.tax_doctype = "Purchase Taxes and Charges"
			self.select_columns = """
				name as invoice_number,
				supplier,
				posting_date as posting_date_unformatted,
				bill_date,
				bill_no,
				taxes_and_charges,
				base_grand_total,
				base_net_total,
				supplier_gstin,
				place_of_supply,
				ecommerce_gstin,
				reverse_charge,
				invoice_type,
				return_against,
				is_return,
				invoice_type,
				export_type,
				reason_for_issuing_document,
				eligibility_for_itc,
				itc_integrated_tax,
				itc_central_tax,
				itc_state_tax,
				itc_cess_amount
			"""

	def get_data(self):
		self.get_igst_invoices()
		for inv, items_based_on_rate in self.items_based_on_tax_rate.items():
			invoice_details = self.invoices.get(inv)
			for rate, items in items_based_on_rate.items():
				row, taxable_value = self.get_row_data_for_invoice(inv, invoice_details, rate, items)
				tax_amount = taxable_value * rate / 100
				if inv in self.igst_invoices:
					row += [tax_amount, 0, 0]
				else:
					row += [0, tax_amount / 2, tax_amount / 2]

				row += [
					self.invoice_cess.get(inv),
					invoice_details.get('eligibility_for_itc'),
					invoice_details.get('itc_integrated_tax'),
					invoice_details.get('itc_central_tax'),
					invoice_details.get('itc_state_tax'),
					invoice_details.get('itc_cess_amount')
				]
				if self.filters.get("type_of_business") ==  "CDNR":
					row.append("Y" if invoice_details.posting_date <= date(2017, 7, 1) else "N")
					row.append("C" if invoice_details.return_against else "R")

				self.data.append(row)
	def get_igst_invoices(self):
		self.igst_invoices = []
		for d in self.tax_details:
			is_igst = True if d[1] in self.gst_accounts.igst_account else False
			if is_igst and d[0] not in self.igst_invoices:
				self.igst_invoices.append(d[0])

	def get_conditions(self):
		conditions = ""
		if self.filters.get("letter_head"):
			conditions += " AND letter_head = '%s'" %(self.filters.get("letter_head"))

		for opts in (("company", " and company=%(company)s"),
			("from_date", " and posting_date>=%(from_date)s"),
			("to_date", " and posting_date<=%(to_date)s")):
				if self.filters.get(opts[0]):
					conditions += opts[1]

		if self.filters.get("type_of_business") ==  "B2B":
			conditions += "and ifnull(invoice_type, '') != 'Export' and is_return != 1 "

		elif self.filters.get("type_of_business") ==  "CDNR":
			conditions += """ and is_return = 1 """

		return conditions

	def get_columns(self):
		if self.filters.get("type") == 'Sales Invoice':
			self.tax_columns = [
				{
					"fieldname": "rate",
					"label": "Rate",
					"fieldtype": "Int",
					"width": 60
				},
				{
					"fieldname": "taxable_value",
					"label": "Taxable Value",
					"fieldtype": "Currency",
					"width": 100
				}
			]
			self.other_columns = []
			self.invoice_columns = [
				{
					"fieldname": "posting_date_unformatted",
					"label": "Invoice Date",
					"fieldtype": "Date",
					"width": 80
				},
				{
					"fieldname": "invoice_number",
					"label": "Invoice Number",
					"fieldtype": "Link",
					"options": "Sales Invoice",
					"width": 120
				},
				{
					"fieldname": "base_net_total",
					"label": "Net Total",
					"fieldtype": "Currency",
					"width": 80
				},
				{
					"fieldname": "base_grand_total",
					"label": "Grand Total",
					"fieldtype": "Currency",
					"width": 80
				},
				{
					"fieldname": "customer",
					"label": "Customer Link",
					"fieldtype": "Link",
					"options": "Customer",
					"width": 200
				},
				{
					"fieldname": "taxes_and_charges",
					"label": "Tax Link",
					"fieldtype": "Link",
					"options": "Sales Taxes and Charges Template",
					"width": 150
				},
				{
					"fieldname": "is_export",
					"label": "Is EXP",
					"fieldtype": "Data",
					"width": 30
				},
				{
					"fieldname": "gst_paid_on_export",
					"label": "GST Paid on EXP",
					"fieldtype": "Data",
					"width": 30
				},
				{
					"fieldname": "export_shb_no",
					"label": "EXP SHB #",
					"fieldtype": "Data",
					"width": 30
				},
				{
					"fieldname": "export_shb_date",
					"label": "EXP SHB Date",
					"fieldtype": "Data",
					"width": 30
				},
				{
					"fieldname": "export_destination_country_code",
					"label": "Exp Dest Country Code",
					"fieldtype": "Data",
					"width": 30
				},
				{
					"fieldname": "customer_address",
					"label": "Billing Address Link",
					"fieldtype": "Link",
					"options": "Address",
					"width": 80
				},
				{
					"fieldname": "customer_address",
					"label": "Billing Address Link",
					"fieldtype": "Link",
					"options": "Address",
					"width": 80
				},
				]
		elif self.filters.get("type") == 'Purchase Invoice':
			self.tax_columns = [
				{
					"fieldname": "rate",
					"label": "Rate",
					"fieldtype": "Int",
					"width": 60
				},
				{
					"fieldname": "taxable_value",
					"label": "Taxable Value",
					"fieldtype": "Currency",
					"width": 100
				},
				{
					"fieldname": "integrated_tax_paid",
					"label": "Integrated Tax Paid",
					"fieldtype": "Currency",
					"width": 100
				},
				{
					"fieldname": "central_tax_paid",
					"label": "Central Tax Paid",
					"fieldtype": "Currency",
					"width": 100
				},
				{
					"fieldname": "state_tax_paid",
					"label": "State/UT Tax Paid",
					"fieldtype": "Currency",
					"width": 100
				},
				{
					"fieldname": "cess_amount",
					"label": "Cess Paid",
					"fieldtype": "Currency",
					"width": 100
				},
				{
					"fieldname": "eligibility_for_itc",
					"label": "Eligibility For ITC",
					"fieldtype": "Data",
					"width": 100
				},
				{
					"fieldname": "itc_integrated_tax",
					"label": "Availed ITC Integrated Tax",
					"fieldtype": "Currency",
					"width": 100
				},
				{
					"fieldname": "itc_central_tax",
					"label": "Availed ITC Central Tax",
					"fieldtype": "Currency",
					"width": 100
				},
				{
					"fieldname": "itc_state_tax",
					"label": "Availed ITC State/UT Tax",
					"fieldtype": "Currency",
					"width": 100
				},
				{
					"fieldname": "itc_cess_amount",
					"label": "Availed ITC Cess ",
					"fieldtype": "Currency",
					"width": 100
				}
			]
			self.other_columns = []
			self.invoice_columns = [
				{
					"fieldname": "invoice_number",
					"label": "PI #",
					"fieldtype": "Link",
					"options": "Purchase Invoice",
					"width": 120
				},
				{
					"fieldname": "posting_date_unformatted",
					"label": "PI Posting Date",
					"fieldtype": "Date",
					"width": 80
				},
				{
					"fieldname": "bill_date",
					"label": "Supplier PI Date",
					"fieldtype": "Date",
					"width": 80
				},
				{
					"fieldname": "bill_no",
					"label": "Supplier PI No",
					"fieldtype": "Data",
					"width": 80
				},
				{
					"fieldname": "supplier",
					"label": "Supplier Link",
					"fieldtype": "Link",
					"options": "Supplier",
					"width": 200
				},
				{
					"fieldname": "taxes_and_charges",
					"label": "Tax Link",
					"fieldtype": "Link",
					"options": "Purchase Taxes and Charges Template",
					"width": 150
				},
				{
					"fieldname": "supplier_gstin",
					"label": "GSTIN of Supplier",
					"fieldtype": "Data",
					"width": 130
				},
				{
					"fieldname": "place_of_supply",
					"label": "Place of Supply",
					"fieldtype": "Data",
					"width": 120
				},
				{
					"fieldname": "invoice_value",
					"label": "Invoice Value",
					"fieldtype": "Currency",
					"width": 120
				},
				{
					"fieldname": "reverse_charge",
					"label": "Reverse Charge",
					"fieldtype": "Data",
					"width": 80
				},
				{
					"fieldname": "invoice_type",
					"label": "Invoice Type",
					"fieldtype": "Data",
					"width": 80
				}
				]
		self.columns = self.invoice_columns + self.tax_columns + self.other_columns