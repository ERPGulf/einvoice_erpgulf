"""United Arab Emirates: PINT-AE profile (FTA, DCTCE 5-corner model).

Everything in this module is UAE e-invoicing law or PINT-AE convention, not
anything a particular Accredited Service Provider decided. The UAE adapters
under countries/ae/providers import from here; the common code reaches the
same values through ``profile`` (an AeProfile instance) and never imports
this module by name.

Module-level names (endpoint, tin_from_trn, emirate_code, vat_category_code,
resolve_address_codes, the code tables) are kept for the existing adapters;
new adapters should use ``self.profile.<method>`` so they can serve a second
country without edits.
"""

import frappe
from frappe import _

from einvoice_erpgulf.core import codes, numbers
from einvoice_erpgulf.core.standards.pint.profile import PintProfile

# ---------------------------------------------------------------- code tables
# Emirate name (as on Address.emirate) -> Peppol country subdivision code
EMIRATE_CODES = {
	"abu dhabi": "AUH",
	"dubai": "DXB",
	"sharjah": "SHJ",
	"ajman": "AJM",
	"umm al quwain": "UAQ",
	"ras al khaimah": "RAK",
	"fujairah": "FUJ",
}
EMIRATE_NAMES_TEXT = "Abu Dhabi, Dubai, Sharjah, Ajman, Umm Al Quwain, Ras Al Khaimah or Fujairah"

# UNCL5305 VAT category codes allowed by PINT-AE
VAT_CODES = ("S", "Z", "E", "AE", "O", "N")

# Full labels (lower-cased) accepted on custom_vat_category and Item Tax Template
VAT_CATEGORY_LABELS = {
	"s - standard rated": "S",
	"standard rated": "S",
	"s": "S",
	"z - zero rated": "Z",
	"zero rated": "Z",
	"z": "Z",
	"e - exempt from tax": "E",
	"exempt from tax": "E",
	"e": "E",
	"ae - vat reverse charge": "AE",
	"vat reverse charge": "AE",
	"reverse charge": "AE",
	"ae": "AE",
	"o - not subject to vat": "O",
	"not subject to vat": "O",
	"o": "O",
	"n - margin scheme": "N",
	"margin scheme": "N",
	"n": "N",
}

# custom_legal_registration_identifier_type option -> BTAE-15/16 type code
LEGAL_REGISTRATION_TYPES = {
	"Commercial/Trade license": "TL",
	"Emirates ID": "EID",
	"Passport": "PAS",
	"Cabinet decision": "CD",
}

# Credit-note / debit-note reason codes (BTAE-19 discrepancy codes)
DISCREPANCY_CODES = {"DL8.61.1.A", "DL8.61.1.B", "DL8.61.1.C", "DL8.61.1.D", "DL8.61.1.E", "VD"}


class AeProfile(PintProfile):
	country_code = "AE"
	country_name = "United Arab Emirates"
	pint_suffix = "ae-1"
	authority_name = "Federal Tax Authority"
	authority_short = "FTA"
	provider_label = "Accredited Service Provider"
	provider_short = "ASP"

	currency = "AED"
	currency_decimals = 2
	endpoint_scheme = "0235"  # TIN-based Peppol participant scheme

	tax_codes = VAT_CODES
	tax_category_labels = VAT_CATEGORY_LABELS
	region_field = "emirate"
	region_codes = EMIRATE_CODES
	region_required = True  # PINT-AE IBR-144-ae
	uom_piece_code = "EA"

	legal_registration_types = LEGAL_REGISTRATION_TYPES
	discrepancy_codes = DISCREPANCY_CODES

	def endpoint_id_from_tax_id(self, tax_id):
		"""Scheme 0235 uses the 10-digit TIN: the first 10 digits of the 15-digit TRN."""
		return tin_from_trn(tax_id)

	def region_names(self):
		return [EMIRATE_NAMES_TEXT]

	# PINT-AE transaction type flags (BTAE-02) are read off the invoice
	transaction_type_pattern = staticmethod(lambda doc: transaction_type_pattern(doc))
	transaction_type_flags = staticmethod(lambda doc: transaction_type_flags(doc))


PROFILE = AeProfile()

# ---------------------------------------------------------------- module-level API used by the AE adapters
TWO = numbers.quantum(2)
SIX = numbers.SIX
dec = numbers.dec
qty = numbers.qty
num = numbers.num


def money(value):
	return numbers.money(value, 2)


DEFAULT_ENDPOINT_SCHEME = AeProfile.endpoint_scheme
PROCESS_BILLING = PROFILE.process_billing
PROCESS_SELF_BILLING = PROFILE.process_self_billing

INVOICE_TYPE_TAX_INVOICE = codes.INVOICE_TYPE_TAX_INVOICE
INVOICE_TYPE_CREDIT_NOTE = codes.INVOICE_TYPE_CREDIT_NOTE
INVOICE_TYPE_SELF_BILLED_INVOICE = codes.INVOICE_TYPE_SELF_BILLED_INVOICE
INVOICE_TYPE_SELF_BILLED_CREDIT_NOTE = codes.INVOICE_TYPE_SELF_BILLED_CREDIT_NOTE

uom_to_unece_table = codes.uom_to_unece_table
UOM_TO_UNECE = codes.UOM_TO_UNECE
uom_to_unece = codes.uom_to_unece
split_code = codes.split_code
country_code = codes.country_code


def endpoint(peppol_id, default_scheme=DEFAULT_ENDPOINT_SCHEME):
	"""'0235:1234567890' -> ('1234567890', '0235'); '1234567890' -> (id, default scheme)."""
	peppol_id = (peppol_id or "").strip()
	if ":" in peppol_id:
		scheme, value = peppol_id.split(":", 1)
		return value.strip(), scheme.strip()
	return peppol_id, default_scheme


def tin_from_trn(trn):
	"""The Peppol endpoint (scheme 0235) uses the 10-digit TIN, which is the
	first 10 digits of the 15-digit TRN."""
	digits = "".join(c for c in (trn or "") if c.isdigit())
	return digits[:10]


def emirate_code(emirate_name):
	"""'Dubai' -> 'DXB'; unknown or empty -> None."""
	if not emirate_name:
		return None
	return EMIRATE_CODES.get(emirate_name.strip().lower())


def vat_category_code(label):
	"""'S - Standard rated' -> 'S'. Accepts the full label, the plain text or
	the bare code; anything else throws."""
	if not label:
		return None
	key = label.strip().lower()
	code = VAT_CATEGORY_LABELS.get(key)
	if not code:
		code = label.split(" - ")[0].strip().upper()
	if code not in VAT_CODES:
		frappe.throw(_("Invalid VAT Category: {0}. Must be one of S, Z, E, AE, O, N.").format(label))
	return code


def transaction_type_pattern(doc):
	"""custom_invoice_transaction_type_code ('1XXXXXX : Free trade zone
	transaction') -> the 7-character X/1 pattern, or None when unset."""
	raw = (
		doc.get("custom_invoice_transaction_type_code")
		if hasattr(doc, "get")
		else getattr(doc, "custom_invoice_transaction_type_code", None)
	)
	if not raw:
		return None
	return raw.split(":")[0].strip()


def transaction_type_flags(doc):
	"""custom_invoice_transaction_type_code -> the 8-character 0/1 flag
	string BTAE-02 needs ('1XXXXXX' -> '10000000')."""
	raw = transaction_type_pattern(doc) or ""
	bits = "".join("1" if c == "1" else "0" for c in raw)
	return (bits + "00000000")[:8]


def resolve_address_codes(address):
	"""ISO country code and Peppol subdivision for an Address doc (PINT-AE rules)."""
	return PROFILE.resolve_address_codes(address)
