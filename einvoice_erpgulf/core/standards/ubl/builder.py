"""UBL 2.1 Invoice / CreditNote builder, driven by a country profile.

Used by adapters whose provider takes the Peppol document itself (every
European access point; some PINT providers) rather than a provider JSON.
The builder reads only ERPNext fields and asks the profile for every code,
so the same function produces a PINT-OM invoice and a BIS 3.0 invoice.

    from einvoice_erpgulf.core.standards.ubl.builder import build_ubl
    xml_bytes = build_ubl(doc, profile, supplier=..., customer=...)

``supplier`` / ``customer`` are PartyInfo dicts (see ``party_from_company`` /
``party_from_customer``); adapters may build them themselves when a
provider needs something unusual.

Coverage: header, parties, payment means and terms, document-level
allowance (invoice discount), tax total with one subtotal per category and
rate, legal monetary total, lines with price and classified tax category.
That is enough for EN 16931 and PINT "billing" and "self-billing"; country
extensions (PINT-AE's BTAE fields, XRechnung's Leitweg-ID) are added by the
country profile through ``extend_header`` / ``extend_line`` hooks.
"""

import xml.etree.ElementTree as ET
from collections import OrderedDict

import frappe

NS = {
	"inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
	"cn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
	"cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
	"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
for prefix, uri in NS.items():
	if prefix not in ("inv", "cn"):
		ET.register_namespace(prefix, uri)


def _q(prefix, tag):
	return f"{{{NS[prefix]}}}{tag}"


def _add(parent, prefix, tag, text=None, **attrs):
	el = ET.SubElement(parent, _q(prefix, tag), {k: str(v) for k, v in attrs.items() if v is not None})
	if text is not None:
		el.text = str(text)
	return el


# ---------------------------------------------------------------- party helpers
def party_from_company(company_name, profile, address_name=None, endpoint_id=None):
	"""PartyInfo for our own company (seller on a sales invoice)."""
	company = frappe.get_doc("Company", company_name)
	address = frappe.get_doc("Address", address_name) if address_name else None
	tax_id = company.get("tax_id")
	if endpoint_id:
		endpoint_id, scheme = profile.endpoint(endpoint_id)
	else:
		endpoint_id, scheme = profile.endpoint_id_from_tax_id(tax_id), profile.endpoint_scheme
	return {
		"name": company.company_name,
		"registration_name": company.company_name,
		"tax_id": tax_id,
		"endpoint_id": endpoint_id,
		"endpoint_scheme": scheme,
		"address": address,
		"email": company.get("email"),
		"phone": company.get("phone_no"),
	}


def party_from_customer(customer_name, profile, address_name=None, endpoint_id=None):
	"""PartyInfo for the buyer (Customer) or, on a purchase, the Supplier."""
	doctype = "Customer" if frappe.db.exists("Customer", customer_name) else "Supplier"
	party = frappe.get_doc(doctype, customer_name)
	address = frappe.get_doc("Address", address_name) if address_name else None
	tax_id = party.get("tax_id")
	stored = party.get("custom_peppol_id")
	endpoint_id, scheme = profile.endpoint(stored) if stored else (endpoint_id or profile.endpoint_id_from_tax_id(tax_id), profile.endpoint_scheme)
	return {
		"name": party.get("customer_name") or party.get("supplier_name") or customer_name,
		"registration_name": party.get("customer_name") or party.get("supplier_name") or customer_name,
		"tax_id": tax_id,
		"endpoint_id": endpoint_id,
		"endpoint_scheme": scheme,
		"address": address,
		"email": party.get("email_id"),
		"phone": party.get("mobile_no"),
	}


def _party(parent, prefix_tag, info, profile):
	wrapper = _add(parent, "cac", prefix_tag)
	party = _add(wrapper, "cac", "Party")
	if info.get("endpoint_id"):
		_add(party, "cbc", "EndpointID", info["endpoint_id"], schemeID=info.get("endpoint_scheme"))
	if info.get("tax_id"):
		pid = _add(party, "cac", "PartyIdentification")
		_add(pid, "cbc", "ID", info["tax_id"], schemeID=info.get("endpoint_scheme"))
	pn = _add(party, "cac", "PartyName")
	_add(pn, "cbc", "Name", info["name"])

	address = info.get("address")
	pa = _add(party, "cac", "PostalAddress")
	if address:
		country, subdivision = profile.resolve_address_codes(address)
		_add(pa, "cbc", "StreetName", address.get("address_line1"))
		if address.get("address_line2"):
			_add(pa, "cbc", "AdditionalStreetName", address.get("address_line2"))
		_add(pa, "cbc", "CityName", address.get("city"))
		if address.get("pincode"):
			_add(pa, "cbc", "PostalZone", address.get("pincode"))
		if subdivision:
			_add(pa, "cbc", "CountrySubentity", subdivision)
		c = _add(pa, "cac", "Country")
		_add(c, "cbc", "IdentificationCode", country)
	else:
		c = _add(pa, "cac", "Country")
		_add(c, "cbc", "IdentificationCode", profile.country_code)

	if info.get("tax_id"):
		pts = _add(party, "cac", "PartyTaxScheme")
		_add(pts, "cbc", "CompanyID", info["tax_id"])
		ts = _add(pts, "cac", "TaxScheme")
		_add(ts, "cbc", "ID", "VAT")

	ple = _add(party, "cac", "PartyLegalEntity")
	_add(ple, "cbc", "RegistrationName", info.get("registration_name") or info["name"])
	if info.get("legal_id"):
		_add(ple, "cbc", "CompanyID", info["legal_id"], schemeID=info.get("legal_id_scheme"))

	if info.get("email") or info.get("phone"):
		ct = _add(party, "cac", "Contact")
		if info.get("phone"):
			_add(ct, "cbc", "Telephone", info["phone"])
		if info.get("email"):
			_add(ct, "cbc", "ElectronicMail", info["email"])
	return wrapper


# ---------------------------------------------------------------- tax helpers
def _line_tax(doc, item, profile):
	"""(category code, rate %) for an invoice line."""
	category = None
	if item.get("item_tax_template"):
		category = frappe.db.get_value("Item Tax Template", item.item_tax_template, "custom_vat_category")
	category = profile.tax_category_code(category or doc.get("custom_vat_category") or "S")

	rate = 0.0
	item_rates = item.get("item_tax_rate")
	if item_rates:
		try:
			parsed = frappe.parse_json(item_rates) or {}
			rate = float(next(iter(parsed.values()), 0) or 0)
		except Exception:
			rate = 0.0
	if not rate:
		rate = sum(float(t.rate or 0) for t in doc.get("taxes", []) if t.charge_type in ("On Net Total", "On Previous Row Total", "On Previous Row Amount"))
	if category in ("Z", "E", "AE", "O", "G", "K"):
		rate = 0.0
	return category, rate


# ---------------------------------------------------------------- the builder
def build_ubl(doc, profile, supplier, customer, self_billing=False, note=None):
	"""Return the UBL document as UTF-8 bytes.

	doc: Sales Invoice or Purchase Invoice. supplier / customer: PartyInfo
	dicts (seller and buyer in UBL terms; on a self-billed purchase invoice the
	adapter passes them already swapped).
	"""
	is_credit = bool(doc.get("is_return"))
	root_prefix = "cn" if is_credit else "inv"
	root_tag = "CreditNote" if is_credit else "Invoice"
	root = ET.Element(_q(root_prefix, root_tag))

	process = profile.process_self_billing if self_billing else profile.process_billing
	_add(root, "cbc", "CustomizationID", process["customization_id"])
	_add(root, "cbc", "ProfileID", process["profile_id"])
	_add(root, "cbc", "ID", doc.name)
	_add(root, "cbc", "IssueDate", str(doc.posting_date))
	if not is_credit and doc.get("due_date"):
		_add(root, "cbc", "DueDate", str(doc.due_date))
	type_key = ("self_billed_credit_note" if is_credit else "self_billed_invoice") if self_billing else ("credit_note" if is_credit else "invoice")
	_add(root, "cbc", "CreditNoteTypeCode" if is_credit else "InvoiceTypeCode", profile.invoice_type_codes[type_key])
	if note or doc.get("custom_invoice_note"):
		_add(root, "cbc", "Note", note or doc.get("custom_invoice_note"))
	currency = profile.currency or doc.currency
	_add(root, "cbc", "DocumentCurrencyCode", currency)
	if doc.get("po_no"):
		_add(root, "cbc", "BuyerReference", doc.po_no)

	if is_credit and doc.get("return_against"):
		br = _add(root, "cac", "BillingReference")
		idr = _add(br, "cac", "InvoiceDocumentReference")
		_add(idr, "cbc", "ID", doc.return_against)
		issue = frappe.db.get_value(doc.doctype, doc.return_against, "posting_date")
		if issue:
			_add(idr, "cbc", "IssueDate", str(issue))

	profile.extend_header(root, doc) if hasattr(profile, "extend_header") else None

	_party(root, "AccountingSupplierParty", supplier, profile)
	_party(root, "AccountingCustomerParty", customer, profile)

	# payment means / terms
	means_code = "30"
	if doc.get("custom_payment_means_codes"):
		means_code = profile.split_code(doc.custom_payment_means_codes)[0]
	pm = _add(root, "cac", "PaymentMeans")
	_add(pm, "cbc", "PaymentMeansCode", means_code)
	if doc.get("payment_terms_template"):
		pt = _add(root, "cac", "PaymentTerms")
		_add(pt, "cbc", "Note", doc.payment_terms_template)

	sign = -1 if is_credit else 1
	m = profile.money

	# lines and tax buckets
	buckets = OrderedDict()  # (category, rate) -> [taxable, tax]
	line_total = 0.0
	lines = []
	for idx, item in enumerate(doc.items, start=1):
		category, rate = _line_tax(doc, item, profile)
		net = float(item.net_amount or item.amount or 0) * sign
		line_total += net
		taxable, tax = buckets.setdefault((category, rate), [0.0, 0.0])
		buckets[(category, rate)] = [taxable + net, tax + net * rate / 100.0]
		lines.append((idx, item, category, rate, net))

	# document-level discount
	discount = float(doc.get("discount_amount") or 0) * sign
	allowance_total = 0.0
	if discount:
		allowance_total = discount
		# ERPNext applies the discount before tax when apply_discount_on is Net Total
		ac = _add(root, "cac", "AllowanceCharge")
		_add(ac, "cbc", "ChargeIndicator", "false")
		_add(ac, "cbc", "AllowanceChargeReason", "Discount")
		_add(ac, "cbc", "Amount", m(abs(discount)), currencyID=currency)
		cat_code, cat_rate = next(iter(buckets)) if buckets else ("S", 0.0)
		tc = _add(ac, "cac", "TaxCategory")
		_add(tc, "cbc", "ID", cat_code)
		_add(tc, "cbc", "Percent", profile.num(cat_rate))
		ts = _add(tc, "cac", "TaxScheme")
		_add(ts, "cbc", "ID", "VAT")
		taxable, tax = buckets[(cat_code, cat_rate)]
		buckets[(cat_code, cat_rate)] = [taxable - discount, tax - discount * cat_rate / 100.0]

	tax_total_amount = sum(b[1] for b in buckets.values())
	tt = _add(root, "cac", "TaxTotal")
	_add(tt, "cbc", "TaxAmount", m(abs(tax_total_amount)), currencyID=currency)
	for (category, rate), (taxable, tax) in buckets.items():
		st = _add(tt, "cac", "TaxSubtotal")
		_add(st, "cbc", "TaxableAmount", m(abs(taxable)), currencyID=currency)
		_add(st, "cbc", "TaxAmount", m(abs(tax)), currencyID=currency)
		tc = _add(st, "cac", "TaxCategory")
		_add(tc, "cbc", "ID", category)
		_add(tc, "cbc", "Percent", profile.num(rate))
		reason = doc.get("custom_vat_exemption_reason_code")
		if category in ("E", "AE", "O", "G", "K") and reason:
			code, text = profile.split_code(reason)
			_add(tc, "cbc", "TaxExemptionReasonCode", code)
			_add(tc, "cbc", "TaxExemptionReason", text)
		ts = _add(tc, "cac", "TaxScheme")
		_add(ts, "cbc", "ID", "VAT")

	tax_exclusive = line_total - allowance_total
	tax_inclusive = tax_exclusive + tax_total_amount
	lmt = _add(root, "cac", "LegalMonetaryTotal")
	_add(lmt, "cbc", "LineExtensionAmount", m(abs(line_total)), currencyID=currency)
	_add(lmt, "cbc", "TaxExclusiveAmount", m(abs(tax_exclusive)), currencyID=currency)
	_add(lmt, "cbc", "TaxInclusiveAmount", m(abs(tax_inclusive)), currencyID=currency)
	if allowance_total:
		_add(lmt, "cbc", "AllowanceTotalAmount", m(abs(allowance_total)), currencyID=currency)
	_add(lmt, "cbc", "PayableAmount", m(abs(tax_inclusive)), currencyID=currency)

	qty_tag = "CreditedQuantity" if is_credit else "InvoicedQuantity"
	line_tag = "CreditNoteLine" if is_credit else "InvoiceLine"
	for idx, item, category, rate, net in lines:
		ln = _add(root, "cac", line_tag)
		_add(ln, "cbc", "ID", idx)
		_add(ln, "cbc", qty_tag, profile.qty(abs(float(item.qty or 0))), unitCode=profile.uom_to_unece(item.uom))
		_add(ln, "cbc", "LineExtensionAmount", m(abs(net)), currencyID=currency)
		it = _add(ln, "cac", "Item")
		if item.get("description") and item.description != item.item_name:
			_add(it, "cbc", "Description", frappe.utils.strip_html(item.description)[:1000])
		_add(it, "cbc", "Name", item.item_name or item.item_code)
		if item.get("item_code"):
			sid = _add(it, "cac", "SellersItemIdentification")
			_add(sid, "cbc", "ID", item.item_code)
		ctc = _add(it, "cac", "ClassifiedTaxCategory")
		_add(ctc, "cbc", "ID", category)
		_add(ctc, "cbc", "Percent", profile.num(rate))
		ts = _add(ctc, "cac", "TaxScheme")
		_add(ts, "cbc", "ID", "VAT")
		pr = _add(ln, "cac", "Price")
		_add(pr, "cbc", "PriceAmount", m(abs(float(item.net_rate or item.rate or 0))), currencyID=currency)
		profile.extend_line(ln, doc, item) if hasattr(profile, "extend_line") else None

	ET.register_namespace("", NS[root_prefix])
	return ET.tostring(root, encoding="utf-8", xml_declaration=True)
