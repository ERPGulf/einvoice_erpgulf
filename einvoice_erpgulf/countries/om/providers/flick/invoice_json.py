"""Flick Oman only - builds the PINT-OM JSON body Flick expects.

One builder for both directions:
  Sales Invoice    -> 380 invoice / 381 credit note / 383 debit note,
                      receiving_party = Customer
  Purchase Invoice -> 389 self-billed invoice / 261 self-billed credit note,
                      receiving_party = Supplier

Field names follow https://developer.flick.network/api-references/regional/om
(sandbox: https://sb-om-api.flick.network). Never import from countries/ae/.
"""

import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import flt, getdate

from einvoice_erpgulf.core.codes import country_code_mapping, uom_to_unece_table
from einvoice_erpgulf.core.numbers import dec, money, qty
from einvoice_erpgulf.core.registry import get_profile

OM_PEPPOL_SCHEME = "0248"
BASE_CURRENCY = "OMR"
THREE_DP_CURRENCIES = {"OMR", "BHD", "KWD", "JOD", "TND", "LYD", "IQD"}
ALLOWED_VAT_CATEGORIES = {"S", "E", "O", "Z"}
CREDIT_NOTE_REASONS = {"CAN", "VAT", "VAL", "QTY", "OTH"}
FREQUENCY_CODES = {"DLY", "WKY", "Q15", "MTH", "Q45", "Q60", "QTR", "YRL", "HYR", "OTH"}
PAYMENT_MEANS_CODES = {"1", "10", "20", "30", "31", "42", "48", "49", "55", "58"}
SPECIAL_ZONES = {"SHRFZ", "SEZAD", "SLLFZ", "AFZ", "MO"}
DEFAULT_TRANSACTION_TYPE = "0" * 20
UOM_TABLE = uom_to_unece_table("C62")


# ---------------------------------------------------------------- small helpers
def decimals_for(currency):
    return 3 if (currency or "").upper() in THREE_DP_CURRENCIES else 2


def invoice_uuid(doctype, name):
    """Stable UUID (BTOM-002) per site + invoice, so a credit note can
    reference its original invoice's UUID without storing anything."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{frappe.local.site}/{doctype}/{name}"))


def code_of(label):
    """'VATEX-OM-01' / '30 - Credit transfer' / 'CAN - Cancellation' -> code."""
    if not label:
        return None
    raw = label.strip()
    if " - " in raw:
        return raw.split(" - ", 1)[0].strip()
    return raw


def format_peppol_id(value, party_label):
    """Oman Peppol ID = 0248:OM<10 digits>. Accepts a VATIN (OM1234567890),
    plain digits, or an already formatted id (anything with ':')."""
    value = (value or "").strip().replace(" ", "")
    if not value:
        frappe.throw(_("{0} Peppol ID / VAT number is missing").format(party_label), title=_("Peppol ID Required"))
    if ":" in value:
        return value
    upper = value.upper()
    if upper.startswith("OM"):
        return f"{OM_PEPPOL_SCHEME}:{upper}"
    digits = re.sub(r"\D", "", value)
    if not digits:
        frappe.throw(_("Invalid Peppol ID / VAT number for {0}: {1}").format(party_label, value))
    return f"{OM_PEPPOL_SCHEME}:OM{digits}"


def country_code(country_name, default="OM"):
    if not country_name:
        return default
    return country_code_mapping().get(country_name.lower(), default)


def issue_time(doc):
    t = doc.posting_time
    if not t:
        return "00:00:00"
    if isinstance(t, timedelta):
        s = int(t.total_seconds())
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return frappe.utils.get_time(t).strftime("%H:%M:%S")


def uom_code(uom):
    code = UOM_TABLE.get((uom or "").strip().lower())
    return code or "C62"


def get_address(name):
    return frappe.get_doc("Address", name) if name else None


# ---------------------------------------------------------------- document type
def document_type(doc):
    purchase = doc.doctype == "Purchase Invoice"
    if doc.get("is_return"):
        return "261" if purchase else "381"
    if not purchase and doc.get("is_debit_note"):
        return "383"
    return "389" if purchase else "380"


def transaction_type_code(doc):
    """BTOM-001: 20-bit binary string. Defaults to all zeros (standard
    domestic B2B) when the field is empty."""
    code = (doc.get("custom_om_transaction_type_code") or "").strip() or DEFAULT_TRANSACTION_TYPE
    if not re.fullmatch(r"[01]{20}", code):
        frappe.throw(
            _("Oman Transaction Type Code must be exactly 20 characters of 0/1. Got: {0}").format(code),
            title=_("Invalid Transaction Type Code"),
        )
    return code


# ---------------------------------------------------------------- parties
def receiving_party(doc):
    """Customer for sales, Supplier for self-billed purchase invoices."""
    if doc.doctype == "Purchase Invoice":
        party = frappe.get_doc("Supplier", doc.supplier)
        party_name = party.supplier_name
        address = get_address(doc.get("supplier_address") or party.get("supplier_primary_address"))
        label = _("Supplier")
    else:
        party = frappe.get_doc("Customer", doc.customer)
        party_name = party.customer_name
        address = get_address(doc.get("customer_address") or party.get("customer_primary_address"))
        label = _("Customer")

    if not address:
        frappe.throw(_("{0} address not found on {1}").format(label, doc.name))

    errors = []
    if not address.address_line1:
        errors.append(_("Address Line 1 is missing on {0}").format(address.name))
    if not address.city:
        errors.append(_("City is missing on {0}").format(address.name))
    if not address.country:
        errors.append(_("Country is missing on {0}").format(address.name))
    if errors:
        frappe.throw("<br>".join(errors), title=_("{0} Address Incomplete").format(label))

    result = {
        "legal_name": party_name,
        "trade_name": party_name,
        "peppol_id": format_peppol_id(party.get("custom_peppol_id") or party.tax_id, label),
        "vat_number": party.tax_id or None,
        "street_address": address.address_line1,
        "additional_street_address": address.address_line2 or None,
        "city_address": address.city,
        "postal_zone": address.pincode or None,
        "country_code": country_code(address.country),
        "contact_name": party_name,
        "contact_telephone": address.phone or None,
        "contact_email": address.email_id or None,
    }

    zone = (address.get("custom_om_special_zone") or "").strip()
    if zone:
        if zone not in SPECIAL_ZONES:
            frappe.throw(_("Invalid Oman special zone {0} on {1}").format(zone, address.name))
        result["country_subdivision_code"] = zone

    trade_license = (party.get("custom_trade_license_number") or "").strip()
    if trade_license:
        identifier = {"type": "TL", "value": trade_license}
        authority = (party.get("custom_legal_registration_authority") or "").strip()
        if authority:
            identifier["authority_name"] = authority
        result["identifiers"] = [identifier]
    elif doc.doctype == "Purchase Invoice":
        frappe.throw(
            _("Supplier {0}: Trade License Number is required for self-billed invoices.").format(party.name),
            title=_("Trade License Required"),
        )

    return {k: v for k, v in result.items() if v not in (None, "")}


def delivery(doc):
    """IBG-13, sales only, when a shipping address is set."""
    if doc.doctype != "Sales Invoice" or not doc.get("shipping_address_name"):
        return None
    addr = get_address(doc.shipping_address_name)
    out = {
        "actual_delivery_date": str(doc.posting_date),
        "street_address": addr.address_line1,
        "additional_street_address": addr.address_line2 or None,
        "city_address": addr.city,
        "postal_zone": addr.pincode or None,
        "country_code": country_code(addr.country),
        "recipient_name": doc.get("customer_name"),
    }
    zone = (addr.get("custom_om_special_zone") or "").strip()
    if zone in SPECIAL_ZONES:
        out["country_subdivision_code"] = zone
    return {k: v for k, v in out.items() if v not in (None, "")}


# ---------------------------------------------------------------- payment means
def _company_bank(company, account=None):
    fields = ["iban", "bank_account_no", "account_name", "bank"]
    bank = None
    if account:
        bank = frappe.db.get_value("Bank Account", {"account": account, "is_company_account": 1}, fields, as_dict=True)
    if not bank:
        bank = frappe.db.get_value(
            "Bank Account", {"company": company, "is_company_account": 1, "is_default": 1}, fields, as_dict=True
        )
    return bank


def _payment_entry(code, company, account=None):
    entry = {"payment_means_code": code}
    if code in ("30", "31", "42", "58"):
        bank = _company_bank(company, account)
        if bank and (bank.iban or bank.bank_account_no):
            entry["account_id"] = bank.iban or bank.bank_account_no
            entry["account_name"] = bank.account_name
            swift = frappe.db.get_value("Bank", bank.bank, "swift_number") if bank.bank else None
            if swift:
                entry["financial_institution_branch"] = swift
        elif code == "30":
            frappe.throw(
                _("Payment means 30 (credit transfer) needs an IBAN. Set a default company Bank Account with IBAN for {0}.").format(company),
                title=_("IBAN Required"),
            )
    return entry


def payment_means(doc):
    """IBG-16. Sales: from payment rows (POS) via Mode of Payment's
    Payment Means Code; else the invoice field; else credit transfer when
    the company has a default bank account; else '1' (not defined).
    Not sent on credit notes."""
    if doc.get("is_return"):
        return None

    rows = []
    for pay in doc.get("payments") or []:
        if not pay.mode_of_payment:
            continue
        label = frappe.db.get_value("Mode of Payment", pay.mode_of_payment, "custom_payment_means_codes")
        code = code_of(label)
        if not code:
            frappe.throw(
                _("Select Payment Means Codes on Mode of Payment <b>{0}</b>.").format(pay.mode_of_payment),
                title=_("Payment Means Code Missing"),
            )
        account = frappe.db.get_value(
            "Mode of Payment Account", {"parent": pay.mode_of_payment, "company": doc.company}, "default_account"
        )
        rows.append(_payment_entry(code, doc.company, account))

    if not rows:
        code = code_of(doc.get("custom_payment_means_codes"))
        if not code:
            code = "30" if _company_bank(doc.company) else "1"
        rows.append(_payment_entry(code, doc.company))

    for row in rows:
        if row["payment_means_code"] not in PAYMENT_MEANS_CODES:
            frappe.throw(_("Payment means code {0} is not allowed for Oman").format(row["payment_means_code"]))
    return rows


# ---------------------------------------------------------------- lines and totals
def _vat_category(raw, profile, where):
    if not raw:
        return None
    code = profile.tax_category_labels.get(raw.strip().lower()) or code_of(raw).upper()
    if code not in ALLOWED_VAT_CATEGORIES:
        frappe.throw(
            _("VAT category {0} ({1}) is not supported for Oman. Use S, Z, E or O.").format(raw, where),
            title=_("Invalid VAT Category"),
        )
    return code


def _line_tax(doc, item, profile, default_rate):
    """(vat_category, rate, exemption_code, zero_rating_code) for one item."""
    template = frappe.get_doc("Item Tax Template", item.item_tax_template) if item.item_tax_template else None
    if template:
        rate = flt(template.taxes[0].tax_rate) if template.taxes else default_rate
        category = _vat_category(template.get("custom_vat_category"), profile, template.name)
        exemption = template.get("custom_om_vat_exemption_reason_code")
        zero = template.get("custom_om_zero_rating_reason_code")
    else:
        rate = default_rate
        category = _vat_category(doc.get("custom_vat_category"), profile, doc.name)
        exemption = doc.get("custom_om_vat_exemption_reason_code")
        zero = doc.get("custom_om_zero_rating_reason_code")

    if not category:
        category = "S" if rate > 0 else None
    if not category:
        frappe.throw(
            _("Set VAT Category on {0} or on the Item Tax Template of {1}.").format(doc.name, item.item_code),
            title=_("VAT Category Missing"),
        )
    if category != "S":
        rate = 0.0
    if category == "E" and not exemption:
        frappe.throw(_("VAT Exemption Reason Code (VATEX-OM-xx) is required for exempt item {0}.").format(item.item_code))
    if category == "Z" and not zero:
        frappe.throw(_("Zero Rating Reason Code (VATZR-OM-xx) is required for zero-rated item {0}.").format(item.item_code))
    return category, rate, code_of(exemption) if category == "E" else None, code_of(zero) if category == "Z" else None


def invoice_lines(doc, profile, d):
    """IBG-25 lines plus the VAT breakdown (IBG-23).
    Uses net_amount (after document discount, excluding VAT, whether or
    not taxes are included in the print rate), so no document-level
    allowance is sent and the totals always add up."""
    default_rate = flt(doc.taxes[0].rate) if doc.get("taxes") else 0.0
    lines, breakdown = [], {}

    for idx, item in enumerate(doc.items, 1):
        item_qty = abs(flt(item.qty))
        if not item_qty:
            frappe.throw(_("Quantity must not be zero for item {0}").format(item.item_code))

        kind = (item.get("custom_item_type_codes") or "").strip()[:1].upper()
        hs_code = (item.get("custom_hs_code_") or "").strip() or None
        sac_code = (item.get("custom_sac_code") or "").strip() or None
        if kind not in ("G", "S", "B"):
            frappe.throw(_("Item Type (Goods / Services / Both) is missing for item {0}").format(item.item_code))
        if kind in ("G", "B") and not hs_code:
            frappe.throw(_("HS code is missing for item {0}").format(item.item_code))
        if kind in ("S", "B") and not sac_code:
            frappe.throw(_("Service Accounting Code is missing for item {0}").format(item.item_code))

        category, rate, exemption, zero = _line_tax(doc, item, profile, default_rate)
        net = abs(dec(item.net_amount))
        vat = net * dec(rate) / Decimal(100)

        line = {
            "id": str(idx),
            "quantity": qty(item_qty),
            "uom": uom_code(item.uom),
            "line_extension_amount": money(net, d),
            "name": item.item_name,
            "description": frappe.utils.strip_html(item.description or "") or item.item_name,
            "item_type": "SV" if kind == "S" else "GS",
            "vat_category": category,
            "vat_percentage": money(rate, 2),
            "unit_price": qty(net / dec(item_qty)),
            "base_quantity": "1",
            "line_total_including_vat": money(net + vat, d),
        }
        if hs_code and kind in ("G", "B"):
            line["hs_code"] = hs_code
        if sac_code and kind in ("S", "B"):
            line["service_accounting_code"] = sac_code
        if exemption:
            line["vat_exemption_reason_code"] = exemption
        if zero:
            line["zero_rating_reason_code"] = zero
        if item.get("item_code"):
            line["supplier_item_code"] = item.item_code
        lines.append(line)

        key = (category, rate, exemption)
        bucket = breakdown.setdefault(key, Decimal(0))
        breakdown[key] = bucket + net

    vat_totals, total_net, total_vat = [], Decimal(0), Decimal(0)
    for (category, rate, exemption), taxable in breakdown.items():
        taxable_r = dec(money(taxable, d))
        tax_r = dec(money(taxable * dec(rate) / Decimal(100), d))
        total_net += taxable_r
        total_vat += tax_r
        row = {
            "tax_category": category,
            "taxable_amount": float(taxable_r),
            "tax_amount": float(tax_r),
            "tax_rate": float(rate),
        }
        if exemption:
            row["tax_exemption_reason_code"] = exemption
        vat_totals.append(row)

    return lines, vat_totals, total_net, total_vat


def invoice_totals(doc, total_net, total_vat, d, exchange_rate):
    totals = {
        "line_extension_amount": money(total_net, d),
        "tax_exclusive_amount": money(total_net, d),
        "vat_amount": money(total_vat, d),
        "tax_inclusive_amount": money(total_net + total_vat, d),
        "allowance_total_amount": money(0, d),
        "charge_total_amount": money(0, d),
        "prepaid_amount": money(0, d),
        "payable_rounding_amount": money(0, d),
        "payable_amount": money(total_net + total_vat, d),
    }
    if exchange_rate is not None:
        rate = dec(exchange_rate)
        totals["vat_amount_in_omr"] = money(total_vat * rate, 3)
        totals["total_amount_in_omr"] = money((total_net + total_vat) * rate, 3)
    return totals


# ---------------------------------------------------------------- header parts
def exchange_rate_to_omr(doc):
    if (doc.currency or "").upper() == BASE_CURRENCY:
        return None
    company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
    if company_currency != BASE_CURRENCY:
        frappe.throw(_("Company currency must be OMR to submit Oman e-invoices in a foreign currency."))
    if not doc.conversion_rate:
        frappe.throw(_("Exchange rate is required when the invoice currency is not OMR."))
    return qty(doc.conversion_rate)


def due_date(doc):
    if doc.get("is_return") or flt(doc.grand_total) <= 0:
        return None
    issue = getdate(doc.posting_date)
    due = getdate(doc.due_date) if doc.get("due_date") else issue
    return str(max(due, issue))


def invoice_period(doc):
    code = (doc.get("custom_frequency_billing_code_list") or "").strip()
    if not code:
        return None
    if code not in FREQUENCY_CODES:
        frappe.throw(_("Invalid Frequency of Billing code {0}").format(code))
    end = doc.get("due_date") or doc.posting_date
    end = max(getdate(end), getdate(doc.posting_date))
    return {"start_date": str(doc.posting_date), "end_date": str(end), "description_code": code}


def notes(doc):
    text = (doc.get("custom_invoice_note") or "").strip()
    if (doc.get("custom_frequency_billing_code_list") or "") == "OTH" and not text:
        frappe.throw(_("Invoice Note is required when Frequency of Billing is OTH."))
    return [{"text": text}] if text else None


def order_reference(doc):
    if doc.doctype != "Sales Invoice":
        return None
    po = doc.get("po_no")
    so = next((i.sales_order for i in doc.items if i.get("sales_order")), None)
    if not po and not so:
        return None
    return {k: v for k, v in {"po_number": po, "so_number": so}.items() if v}


def preceding_invoice(doc):
    """IBG-03 for credit / debit notes."""
    if not (doc.get("is_return") or doc.get("is_debit_note")):
        return None, None
    original = doc.get("return_against")
    if original:
        date = frappe.db.get_value(doc.doctype, original, "posting_date")
        ref = {"id": original, "uuid": invoice_uuid(doc.doctype, original), "issue_date": str(date)}
        return [ref], original
    manual = doc.get("custom_return_against_for_einvoice")
    if manual:
        return [{"id": manual}], manual
    frappe.throw(_("Credit / Debit Note {0} must reference the original invoice (Return Against).").format(doc.name))


def credit_note_reason(doc):
    if not doc.get("is_return"):
        return None
    code = (code_of(doc.get("custom_om_credit_note_reason_code")) or "").upper()
    if code not in CREDIT_NOTE_REASONS:
        frappe.throw(
            _("Select an Oman Credit Note Reason (CAN, VAT, VAL, QTY or OTH) on {0}.").format(doc.name),
            title=_("Credit Note Reason Required"),
        )
    return code


# ---------------------------------------------------------------- builder
def build_invoice_json(doctype, name):
    doc = frappe.get_doc(doctype, name)
    if not frappe.db.get_value("Company", doc.company, "custom_einvoice_enabled"):
        frappe.throw(_("E-invoicing is not enabled on Company {0}.").format(doc.company))

    profile = get_profile(doc.company)
    d = decimals_for(doc.currency)
    rate = exchange_rate_to_omr(doc)
    lines, vat_totals, total_net, total_vat = invoice_lines(doc, profile, d)
    process = profile.process_self_billing if doctype == "Purchase Invoice" else profile.process_billing
    references, _original = preceding_invoice(doc)

    invoice = {
        "document_identifier": doc.name,
        "uuid": invoice_uuid(doctype, doc.name),
        "issue_date": str(doc.posting_date),
        "issue_time": issue_time(doc),
        "due_date": due_date(doc),
        "document_type": document_type(doc),
        "note": notes(doc),
        "document_currency": (doc.currency or BASE_CURRENCY).upper(),
        "currency_exchange_rate": rate,
        "accounting_cost": doc.get("cost_center"),
        "buyer_reference": doc.get("po_no") if doctype == "Sales Invoice" else doc.get("bill_no"),
        "invoice_period": invoice_period(doc),
        "order_reference": order_reference(doc),
        "credit_note_reason_code": credit_note_reason(doc),
        "document_references": references,
        "receiving_party": receiving_party(doc),
        "delivery": delivery(doc),
        "payment_means": payment_means(doc),
        "invoice_lines": lines,
        "vat_totals": vat_totals,
        "invoice_totals": invoice_totals(doc, total_net, total_vat, d, rate),
        "metadata": {
            "transaction_type_code": transaction_type_code(doc),
            "customization_id": process["customization_id"],
        },
    }
    return {k: v for k, v in invoice.items() if v not in (None, "", [])}


def json_file_name(name):
    return f"{name}_om_invoice.json"


def save_and_attach_invoice_json(doctype, name):
    """Build the JSON and attach it to the invoice, replacing an earlier copy."""
    invoice_json = build_invoice_json(doctype, name)
    file_name = json_file_name(name)

    for f in frappe.get_all(
        "File",
        filters={"attached_to_doctype": doctype, "attached_to_name": name, "file_name": file_name},
        pluck="name",
    ):
        frappe.delete_doc("File", f, force=1, ignore_permissions=True)

    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": file_name,
            "is_private": 1,
            "content": json.dumps(invoice_json, indent=4, ensure_ascii=False),
            "attached_to_doctype": doctype,
            "attached_to_name": name,
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()  # nosemgrep: frappe-manual-commit

    return {"file_name": file_doc.file_name, "file_url": file_doc.file_url, "json": invoice_json}


@frappe.whitelist()
def send_invoice_json(doctype: str, invoice_number: str):
    """Preview button: build and attach the JSON without sending it."""
    if doctype not in ("Sales Invoice", "Purchase Invoice"):
        frappe.throw(_("Unsupported doctype {0}").format(doctype))
    frappe.has_permission(doctype, "read", invoice_number, throw=True)
    result = save_and_attach_invoice_json(doctype, invoice_number)
    return {"message": _("Invoice JSON generated and attached"), "file_url": result["file_url"]}
