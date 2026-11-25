"""KGRN only - builds the Outbound API v2 request (UAE PINT V2.0 structure,
the "Business Central" 217-field JSON plus PushState) for a Sales Invoice.

Spec: KGRN-Outbound-API-v2.md / API-Structure.json from KGRN.
KGRN recomputes LegalMonetaryTotal and the VAT breakdown from the lines and
allowances, so the lines are what has to be right. Lines go at their net
amount (after ERPNext's distributed Additional Discount), so no
document-level allowance is sent - that also keeps margin-scheme invoices
valid (ibr-114/115/116-ae).

Rules worth knowing:
  * 16 fields are JSON numbers (totals, line net, prices, line allowance
    amounts); everything else is a string, and codes stay strings
    ("00000000", "0235").
  * ReferenceNumber is KGRN's key: resending it updates the same invoice
    until it is cleared.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, get_time

from einvoice_erpgulf.core.codes import country_code, split_code, uom_to_unece
from einvoice_erpgulf.countries.ae.profile import (
	LEGAL_REGISTRATION_TYPES,
	emirate_code,
	endpoint,
	tin_from_trn,
	transaction_type_flags,
	vat_category_code,
)

EAS_SCHEME = "0235"
DEEMED_SUPPLY_BUYER = "9900000097"  # notional buyer for deemed supply
BANK_PAYMENT_CODES = ("30", "31", "42", "58")
ITEM_TYPES = {"G": "Goods", "S": "Services", "B": "Both"}


# ---------------------------------------------------------------- small helpers
def _s(value):
	"""String field: None -> ''."""
	return "" if value is None else str(value).strip()


def _n(value):
	"""Numeric field: always a positive float rounded to 2 (returns are negative in ERPNext)."""
	return round(abs(flt(value)), 2)


def _date(value):
	return getdate(value).strftime("%Y-%m-%d") if value else ""


def _time(value):
	return get_time(value).strftime("%H:%M:%S") if value else ""


def _flags(doc):
	"""8-position BTAE-02 flags: FTZ, deemed, margin, summary, continuous, agent, e-commerce, export."""
	return transaction_type_flags(doc)


def _address(name):
	return frappe.get_doc("Address", name) if name else None


def _company_address(doc):
	if doc.get("company_address"):
		return _address(doc.company_address)
	name = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Company", "link_name": doc.company, "parenttype": "Address"},
		"parent",
	)
	return _address(name)


def _customer_address(doc, customer):
	return _address(doc.get("customer_address") or customer.get("customer_primary_address"))


def _postal(addr, profile):
	"""(street, additional street, city, emirate code, postcode, country code)."""
	if not addr:
		return ("", "", "", "", "", "")
	cc, sub = profile.resolve_address_codes(addr)
	return (
		_s(addr.address_line1),
		_s(addr.address_line2),
		_s(addr.city),
		_s(emirate_code(addr.get("emirate")) or sub),
		_s(addr.pincode),
		cc,
	)


def _legal_registration(party_doc):
	"""(type code, number, authority) from the Company / Customer legal registration fields."""
	number = _s(party_doc.get("custom_trade_license_number"))
	if not number:
		return ("", "", "")
	label = party_doc.get("custom_legal_registration_identifier_type") or "Commercial/Trade license"
	return (
		LEGAL_REGISTRATION_TYPES.get(label, "TL"),
		number,
		_s(party_doc.get("custom_legal_registration_authority")),
	)


def _registration_slots(type_code, number):
	"""The four per-type slots of the V2.0 structure; only the matching one is filled."""
	return {
		"TL": number if type_code == "TL" else "",
		"EID": number if type_code == "EID" else "",
		"PAS": number if type_code == "PAS" else "",
		"CD": number if type_code == "CD" else "",
	}


def _peppol_tin(value):
	"""'0235:1234567890', a TIN or a 15-digit TRN -> '1234567890'."""
	tin, _scheme = endpoint(value)
	return tin_from_trn(tin) or tin


# ---------------------------------------------------------------- header pieces
def invoice_type_code(doc):
	out_of_scope = vat_category_code(doc.get("custom_vat_category")) == "O" if doc.get("custom_vat_category") else False
	if doc.is_return:
		return "81" if out_of_scope else "381"
	return "480" if out_of_scope else "380"


def _due_date(doc, flags):
	"""Credit notes and deemed supply carry no due date; otherwise due date when an amount is owed."""
	if doc.is_return or flags[1] == "1" or flt(doc.grand_total) <= 0:
		return ""
	due = getdate(doc.due_date or doc.posting_date)
	if due < getdate(doc.posting_date):
		due = getdate(doc.posting_date)
	return _date(due)


def _invoice_period(doc, flags):
	freq = _s(doc.get("custom_frequency_billing_code_list"))
	summary = flags[3] == "1"
	if summary and not freq:
		frappe.throw(_("Frequency of Billing is mandatory for a Summary Invoice (00010000)."))
	if not freq:
		return "", ""
	start = getdate(doc.posting_date)
	end = getdate(doc.due_date) if doc.due_date and getdate(doc.due_date) >= start else start
	return _date(start), _date(end)


def _billing_reference(doc):
	if not doc.is_return:
		return []
	ref_id, ref_date = "", ""
	if doc.return_against:
		ref_id = doc.return_against
		ref_date = _date(frappe.db.get_value("Sales Invoice", doc.return_against, "posting_date"))
	elif doc.get("custom_return_against_for_einvoice"):
		ref_id = doc.custom_return_against_for_einvoice
	if not ref_id:
		frappe.throw(_("A credit note needs Return Against (or Return Against for E-Invoice)."))
	reason = doc.get("custom_credit_note_reason_code")
	if not reason:
		frappe.throw(_("Select a Credit Note Reason Code on {0}.").format(doc.name))
	return [{
		"BillingReference.InvoiceDocumentReference.ID": ref_id,
		"BillingReference.InvoiceDocumentReference.IssueDate": ref_date,
		"Creditnotereasoncode": split_code(reason)[0],
	}]


# ---------------------------------------------------------------- parties
def _seller(doc, company, settings, profile):
	trn = _s(company.tax_id or settings.get("trn"))
	if not trn:
		frappe.throw(_("Set the Tax ID (TRN) on Company {0}.").format(company.name))
	addr = _company_address(doc)
	if not addr:
		frappe.throw(_("Company {0} has no address.").format(company.name))
	street, street2, city, sub, pin, cc = _postal(addr, profile)
	reg_type, reg_no, authority = _legal_registration(company)
	slots = _registration_slots(reg_type, reg_no)

	return {
		"AccountingSupplierParty.SellerCode": "",
		"AccountingSupplierParty.EndpointID": {
			"AccountingSupplierParty.schemeID": EAS_SCHEME,
			"AccountingSupplierParty.SellerElectronicAddress": _peppol_tin(settings.participant_id or trn),
		},
		"AccountingSupplierParty.PartyIdentification": [{
			"BankassignedcreditorSchemeIdentifier": "",
			"BankassignedcreditorIdentifier": "",
			"Selleridentifier": "",
			"SellerSchemeidentifier": "",
			"Sellerlegalregistrationidentifier": reg_no,
			"SellerLegalSchemeIdentifier": EAS_SCHEME if reg_no else "",
			"SellerLegalRegistrationType": reg_type,
			"SellerCommercialorTradelicense": slots["TL"],
			"SellerEmiratesID": slots["EID"],
			"SellerPassport": slots["PAS"],
			"SellerCabinetDecision": slots["CD"],
			"SellerAuthorityname": authority,
			"SellerPassportIssuingCountrycode": authority if reg_type == "PAS" else "",
		}],
		"AccountingSupplierParty.PartyName": {"AccountingSupplierParty.Name": _s(company.company_name)},
		"AccountingSupplierParty.PostalAddress": {
			"AccountingSupplierParty.StreetName": street,
			"AccountingSupplierParty.AdditionalStreetName": street2,
			"AccountingSupplierParty.Addressline3": "",
			"AccountingSupplierParty.CityName": city,
			"AccountingSupplierParty.CitySubdivisionName": sub,
			"AccountingSupplierParty.PostalZone": pin,
			"AccountingSupplierParty.Country": {"AccountingSupplierParty.IdentificationCode": cc},
		},
		"AccountingSupplierParty.TaxID": {"SellerTaxidentifier": trn, "SellerTaxSchemeID": "VAT"},
		"AccountingSupplierParty.PartyLegalEntity": {
			"AccountingSupplierParty.RegistrationName": _s(company.company_name),
			"Selleradditionallegalinformation": "",
		},
		"AccountingSupplierParty.Contact": {
			"AccountingSupplierParty.Name": _s(company.company_name),
			"AccountingSupplierParty.Telephone": _s(addr.phone or company.get("phone_no")),
			"AccountingSupplierParty.EmailID": _s(addr.email_id or company.get("email")),
		},
	}


def _buyer(doc, customer, profile, flags):
	addr = _customer_address(doc, customer)
	if not addr:
		frappe.throw(_("Customer {0} has no address.").format(customer.name))
	street, street2, city, sub, pin, cc = _postal(addr, profile)
	reg_type, reg_no, authority = _legal_registration(customer)
	slots = _registration_slots(reg_type, reg_no)

	if flags[1] == "1":  # deemed supply goes to the notional participant
		buyer_endpoint = DEEMED_SUPPLY_BUYER
	else:
		raw = customer.get("custom_peppol_id") or customer.tax_id
		if not raw:
			frappe.throw(_("Set Peppol ID or Tax ID on Customer {0}.").format(customer.name))
		buyer_endpoint = _peppol_tin(raw)

	if (doc.is_return or invoice_type_code(doc) in ("480", "81")) and not reg_no:
		frappe.throw(
			_("IBR-136-ae: Customer {0} needs a Legal Registration Number for a credit note or out-of-scope invoice.").format(
				customer.name
			)
		)

	return {
		"BuyerCode": "",
		"EndpointID": {"BuyerElectronicAddressSchemeID": EAS_SCHEME, "BuyerElectronicAddress": buyer_endpoint},
		"PartyIdentification": {"BuyerIdentifier": "", "BuyerSchemeIdentifier": ""},
		"PartyName": {"TradingName": _s(customer.customer_name)},
		"PostalAddress": {
			"StreetName": street,
			"AdditionalStreetName": street2,
			"Addressline3": "",
			"CityName": city,
			"CitySubdivisionName": sub,
			"PostalZone": pin,
			"Country": {"IdentificationCode": cc},
		},
		"PartyTaxScheme": {
			"BuyerTaxID": _s(customer.tax_id),
			"TaxScheme": {
				"BuyerTaxSchemeID": "VAT",
				"Buyerlegalregistrationidentifiertype": reg_type,
				"BuyerCommercialorTradelicense": slots["TL"],
				"BuyerEmiratesID": slots["EID"],
				"BuyerPassport": slots["PAS"],
				"BuyerCabinetDecision": slots["CD"],
				"BuyerAuthorityName": authority,
				"BuyerPassportIssuingCountrycode": authority if reg_type == "PAS" else "",
			},
			"PartyLegalEntity": {
				"RegistrationName": _s(customer.customer_name),
				"CompanyID": {
					"BuyerLegalRegistrationSchemeID": EAS_SCHEME if reg_no else "",
					"BuyerLegalRegistrationIdentifier": reg_no,
				},
			},
			"Contact": {
				"Name": _s(doc.get("contact_display") or customer.customer_name),
				"Telephone": _s(addr.phone or doc.get("contact_mobile")),
				"ElectronicEmail": _s(addr.email_id or doc.get("contact_email")),
			},
		},
	}


def _delivery(doc, profile, flags):
	"""Delivery address is required for export (00000001) and e-commerce (00000010)."""
	needs = flags[6] == "1" or flags[7] == "1"
	addr = _address(doc.get("shipping_address_name")) if needs else None
	if needs and not addr:
		frappe.throw(_("Set a Shipping Address: export / e-commerce invoices need the delivery address."))
	street, street2, city, sub, pin, cc = _postal(addr, profile) if addr else ("",) * 6
	return {
		"Delivery.ActualDeliveryDate": _date(doc.posting_date) if needs else "",
		"Delivery.DeliveryLocation": {
			"Delivery.PartyID": "",
			"Delivery.ID": {"Delivery.schemeID": "", "Delivery.DeliveryIdentifier": ""},
			"Delivery.PostalAddress": {
				"Delivery.StreetName": street,
				"Delivery.AdditionalStreetName": street2,
				"Delivery.AddressLine3": "",
				"Delivery.CitySubdivisionName": sub,
				"Delivery.CityName": city,
				"Delivery.PostalZone": pin,
				"Delivery.Country": {"Delivery.IdentificationCode": cc},
			},
		},
		"Delivery.DeliveryParty": {"Delivery.PartyName": _s(doc.customer_name) if needs else "", "Incoterms": ""},
	}


# ---------------------------------------------------------------- payment
def _payment_means_code(doc):
	for row in doc.get("payments") or []:
		if row.mode_of_payment:
			value = frappe.db.get_value("Mode of Payment", row.mode_of_payment, "custom_payment_means_codes")
			if value:
				return split_code(value)[0]
	return "30"  # credit transfer


def _payment_means(doc):
	code = _payment_means_code(doc)
	account_id = account_name = ""
	if code in BANK_PAYMENT_CODES:
		bank = frappe.db.get_value(
			"Bank Account",
			{"company": doc.company, "is_company_account": 1, "disabled": 0},
			["iban", "bank_account_no", "account_name"],
			order_by="is_default desc",
			as_dict=True,
		)
		if bank:
			account_id = _s(bank.iban or bank.bank_account_no).replace(" ", "")
			account_name = _s(bank.account_name)
		if not account_id:
			frappe.throw(_("ibr-192-ae: add the company IBAN on a company Bank Account for credit transfer."))
	return {
		"PaymentMeansCode": code,
		"PaymentInstructionsID": "",
		"PaymentMeansText": "",
		"Remittanceinformation": "",
		"RemittanceinformationSchemeID": "",
		"PaymentCardPrimaryAccountNumber": "",
		"NetworkID": "",
		"PaymentCardHolderName": "",
		"PaymentAccountSchemeIdentifier": "IBAN" if account_id.upper().startswith("AE") else "",
		"PaymentAccountIdentifier": account_id,
		"PaymentAccountName": account_name,
		"PaymentServiceProviderIdentifier": "",
		"MandateReferenceIdentifier": "",
		"DebitedAccountIdentifier": "",
	}


# ---------------------------------------------------------------- lines
def _line_tax(doc, item):
	"""(VAT category code, rate, exemption code, exemption text, RCM nature code)."""
	default_rate = flt(doc.taxes[0].rate) if doc.get("taxes") else 0
	if item.item_tax_template:
		tpl = frappe.get_cached_doc("Item Tax Template", item.item_tax_template)
		label = tpl.get("custom_vat_category")
		if not label:
			frappe.throw(_("Set VAT Category on Item Tax Template {0}.").format(tpl.name))
		rate = flt(tpl.taxes[0].tax_rate) if tpl.taxes else default_rate
		exemption, rcm = tpl.get("custom_vat_exemption_reason_code"), tpl.get("custom_rcm_nature_code")
	else:
		label = doc.get("custom_vat_category")
		if not label:
			frappe.throw(_("Set VAT Category on {0} or an Item Tax Template on every item.").format(doc.name))
		rate = default_rate
		exemption, rcm = doc.get("custom_vat_exemption_reason_code"), doc.get("custom_rcm_nature_code")

	cat = vat_category_code(label)
	if cat in ("Z", "E", "O"):
		rate = 0
	ex_code, ex_text = split_code(exemption) if (cat == "E" and exemption) else ("", "")
	rcm_code = split_code(rcm)[0] if (cat == "AE" and rcm) else ""
	return cat, rate, ex_code, ex_text, rcm_code


def _line(doc, idx, item):
	type_code = split_code(item.get("custom_item_type_codes"))[0] if item.get("custom_item_type_codes") else ""
	if not type_code:
		frappe.throw(_("Row {0}: set Item Type (Goods / Services / Both) on {1}.").format(idx, item.item_name))
	hs, sac = _s(item.get("custom_hs_code_")), _s(item.get("custom_sac_code"))
	if type_code in ("G", "B") and not hs:
		frappe.throw(_("Row {0}: HS code missing for {1}.").format(idx, item.item_name))
	if type_code in ("S", "B") and not sac:
		frappe.throw(_("Row {0}: SAC code missing for {1}.").format(idx, item.item_name))

	cat, rate, ex_code, ex_text, rcm_code = _line_tax(doc, item)
	qty = abs(flt(item.qty)) or 1
	net = _n(item.net_amount)
	unit = uom_to_unece(item.uom)
	price = round(net / qty, 6)

	return {
		"InvoiceLine.InvoiceLineIdentifier": str(idx),
		"InvoiceLine.ItemMasters": _s(item.item_code),
		"InvoiceLine.InvoicedQuantity": {
			"InvoiceLine.unitCode": unit,
			"InvoiceLine.Note": "",
			"InvoiceLine.Buyeraccountingreference": "",
			"Invoicelineperiodstartdate": "",
			"Invoicelineperiodenddate": "",
			"InvoiceLine.Quantity": str(qty),
		},
		"LineExtensionAmount": {"InvoiceLine.InvoiceLineNetAmount": net},
		"InvoiceLine.OrderLineReference": {"LineID": "", "ReferencedPurchaseOrder": ""},
		"InvoiceLine.DespatchLineReference": {"DespatchAdviceLine": "", "DespatchAdviceReference": ""},
		"InvoiceLine.LineItem": {
			"InvoiceLine.Description": _s(frappe.utils.strip_html(item.description or "") or item.item_name)[:1000],
			"InvoiceLine.Name": _s(item.item_name),
			"Typeofgoodsorservices": rcm_code,
			"Itemtype": ITEM_TYPES[type_code],
			"ServiceAccountingcode": sac,
			"ServiceAccountingSchemeidentifier": "SAC" if sac else "",
			"InvoiceLine.SellerItemIdentification": {"InvoiceLine.SellerItemIdentification.schemeID": _s(item.item_code)},
			"BuyertemIdentification": {"InvoiceLine.BuyertemIdentification.schemeID": ""},
			"StandardItemIdentification": {
				"InvoiceLine.StandardItemIdentification.schemeID": "",
				"InvoiceLine.StandardItemIdentification.SchemeIDCode": "",
			},
			"OriginCountry": {"InvoiceLine.OriginCountry.IdentificationCode": ""},
			"CommodityClassification": {
				"CommodityClassification.listID": "HS" if hs else "",
				"CommodityClassification.Itemcode": hs,
			},
			"ClassifiedTaxCategory": {
				"ClassifiedTaxCategory.ID": cat,
				"ClassifiedTaxCategory.Percent": str(flt(rate)).rstrip("0").rstrip(".") or "0",
				"ClassifiedTaxCategory.TaxSchemeId": "VAT",
				"ClassifiedTaxCategory.TaxExemptionReasonCode": ex_code,
				"ClassifiedTaxCategory.TaxExemptionReason": ex_text if ex_code else "",
			},
			"AttributeDetails": {"Attribute.Name": "", "Attribute.Value": "", "BatchNumber": _s(item.get("batch_no"))},
			"LineAllowanceCharge": [],
			"PriceDetails": {
				"ItemPriceBaseQuantity": "1",
				"ItemPriceBaseQuantityUOMC": unit,
				"ItemNetPrice": price,
				"ItemPriceDiscount": 0,
				"ItemGrossPrice": price,
			},
		},
	}


# ---------------------------------------------------------------- the request
def build_kgrn_payload(doc, settings, profile, push_state="submit"):
	"""Outbound API v2 request body for one Sales Invoice."""
	if doc.doctype != "Sales Invoice":
		frappe.throw(_("KGRN adapter sends Sales Invoices only."))

	company = frappe.get_doc("Company", doc.company)
	customer = frappe.get_doc("Customer", doc.customer)
	flags = _flags(doc)
	start, end = _invoice_period(doc, flags)
	foreign = (doc.currency or "AED").upper() != profile.currency

	beneficiary = ""
	if flags[0] == "1":
		beneficiary = _s(customer.get("custom_fz_beneficiary_id"))
		if not beneficiary:
			frappe.throw(_("FZ Beneficiary ID is mandatory on Customer {0} for Free Trade Zone.").format(customer.name))

	principal = _s(doc.get("custom_principal_id"))
	if flags[5] == "1" and not principal:
		frappe.throw(_("Principal TRN (custom_principal_id) is mandatory for Disclosed Agent Billing."))

	so = next((i.sales_order for i in doc.items if i.get("sales_order")), "") if doc.get("po_no") else ""
	if (_s(doc.get("custom_frequency_billing_code_list")) == "OTH") and not doc.get("custom_invoice_note"):
		frappe.throw(_("Invoice Note is mandatory when Frequency of Billing is OTH."))

	prepaid = _n(doc.get("total_advance"))
	rounding = flt(doc.get("rounding_adjustment")) * (-1 if doc.is_return else 1)
	payable = _n(doc.rounded_total or doc.grand_total) - prepaid
	line_total = sum(_n(i.net_amount) for i in doc.items)

	return {
		"PushState": push_state,
		"ReferenceNumber": doc.name,
		"FinancialYear": str(getdate(doc.posting_date).year),
		"edelivery": "Peppol",
		"InvoiceID": doc.name,
		"IssueDate": _date(doc.posting_date),
		"IssueTime": _time(doc.posting_time),
		"DueDate": _due_date(doc, flags),
		"InvoiceTypeCode": invoice_type_code(doc),
		"Note": _s(doc.get("custom_invoice_note")),
		"DocumentCurrencyCode": (doc.currency or "AED").upper(),
		"VATCurrencyCode": profile.currency if foreign else "",
		"AccountingCost": _s(doc.get("cost_center")),
		"BuyerReference": _s(doc.get("custom_buyer_reference") or doc.get("po_no") or doc.name),
		"InvoicePeriod.StartDate": start,
		"InvoicePeriod.EndDate": end,
		"Invoicetransactiontypecode": flags,
		"VATtaxpointdate": "",
		"PrincipleID": principal,
		"BeneficiaryID": beneficiary,
		"ExchangeRate": str(flt(doc.conversion_rate, 6)) if foreign else "",
		"Purchaseorderreference": _s(doc.get("po_no")),
		"Salesorderreference": _s(so),
		"Despatchadvicereference": "",
		"Receivingadvicereference": "",
		"TotalVATamountinAccountingCurrency": str(_n(doc.base_total_taxes_and_charges)) if foreign else "",
		"Customsreferencenumber": "",
		"FrequencyofBilling": _s(doc.get("custom_frequency_billing_code_list")),
		"ContractDocumentReference.Number": "",
		"ContractValue": "",
		"LotReference": "",
		"ProjectReference": _s(doc.get("project")),
		"BillingReference": _billing_reference(doc),
		"AdditionalInvoiceObject": {"InvoiceObjectId": "", "InvoiceObjectSchemeId": "", "DocumentTypecode": ""},
		"AdditionalDocumentDetails": [],
		"AccountingSupplierParty": _seller(doc, company, settings, profile),
		"AccountingCustomerParty": _buyer(doc, customer, profile, flags),
		"payee": {
			"payeeidentifier": "",
			"payeeidentifierSchemeIdentifier": "",
			"payeename": "",
			"payeelegalRegistrationIdentifier": "",
			"payeelegalRegistrationIdentifierSchemeIdentifier": "",
		},
		"SellerTaxRepresentative": {
			"Sellertaxrepresentativename": "",
			"POSTALADDRESS": {
				"Taxrepresentativeaddressline1": "",
				"Taxrepresentativeaddressline2": "",
				"Taxrepresentativecity": "",
				"Taxrepresentativepostcode": "",
				"Taxrepresentativecountrysubdivision": "",
				"Taxrepresentativeaddressline3": "",
				"Taxrepresentativecountrycode": "",
			},
			"SellerTaxRepresentativeVAT": {"SellertaxrepresentativeVATidentifier": "", "SellerRepVATschemeID": ""},
		},
		"Delivery": _delivery(doc, profile, flags),
		"PaymentMeans": _payment_means(doc),
		"PaymentTerms": {
			"InvoiceTermsID": "",
			"PaymentTerms.Note": _s(doc.get("payment_terms_template")),
			"TermsPaymentInstructionsID": "",
			"TermsInstallmentDueDate": _due_date(doc, flags),
		},
		"AllowanceCharge": [],
		# KGRN recomputes these from the lines; only PrepaidAmount and
		# PayableRoundingAmount are taken as sent.
		"LegalMonetaryTotal": {
			"LegalMonetaryTotal.LineExtensionAmount.InvoiceLineAmount": round(line_total, 2),
			"LegalMonetaryTotal.TaxExclusiveAmount.InvoiceLineAmount": _n(doc.net_total),
			"LegalMonetaryTotal.TaxInclusiveAmount.InvoiceLineAmount": _n(doc.grand_total),
			"LegalMonetaryTotal.TotalDiscountAmount.InvoiceLineAmount": 0,
			"LegalMonetaryTotal.ChargeTotalAmount.InvoiceLineAmount": 0,
			"LegalMonetaryTotal.PrepaidAmount.InvoiceLineAmount": prepaid,
			"PayableRoundingAmount": str(round(rounding, 2)) if rounding else "",
			"LegalMonetaryTotal.PayableAmount.InvoiceLineAmount": round(payable, 2),
			"LegalMonetaryTotal.TotalTaxAmount.InvoiceLineAmount": _n(doc.total_taxes_and_charges),
		},
		"InvoiceLine": [_line(doc, idx, item) for idx, item in enumerate(doc.items, 1)],
	}
