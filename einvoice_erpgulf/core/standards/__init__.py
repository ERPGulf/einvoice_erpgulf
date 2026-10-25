"""Standards: the rulebooks an invoice must follow, independent of any provider.

A *profile* answers every question the common code and the adapters have
about "how is an invoice coded in this country": which Peppol endpoint
scheme, how the tax ID is derived, which region codes, which tax category
codes, how many decimals the currency has, what the customization and
profile IDs are, what the UI should call the tax authority.

    BaseProfile                      core/standards/__init__.py (this file)
    ├── PintProfile                  core/standards/pint/      Peppol PINT (UAE, Oman, SG, MY, A-NZ, JP)
    └── EN16931Profile               core/standards/en16931/   Peppol BIS Billing 3.0 / EU norm (BE, NL, DE, Nordics)

    countries/<cc>/profile.py        subclasses one of the two with that
                                     country's constants and tables

Adapters never import a country profile by name; they use ``self.profile``,
which the registry resolves from the Company's country. That is what lets
one adapter class serve two countries when a provider is accredited in both.
"""

import frappe
from frappe import _

from einvoice_erpgulf.core import codes, numbers


class BaseProfile:
	"""Country-neutral defaults. Everything here is overridable per country."""

	# ---- identity ------------------------------------------------------
	country_code = None  # ISO 3166-1 alpha-2, e.g. "AE"
	country_name = None  # as stored on Company.country / Address.country
	standard = None  # "pint" | "en16931"
	authority_name = "Tax Authority"  # long form, for messages
	authority_short = "Tax Authority"  # short form, for the ribbon
	provider_label = "E-Invoice Provider"  # what the dropdown is called in this country
	provider_short = "Provider"  # used in "Sent to <provider_short>"

	# ---- document identifiers -----------------------------------------
	currency = None  # ISO 4217; None means "use the invoice currency"
	currency_decimals = 2
	endpoint_scheme = None  # Peppol participant identifier scheme (ICD), e.g. "0235"
	process_billing = {"profile_id": "urn:peppol:bis:billing", "customization_id": None}
	process_self_billing = {"profile_id": "urn:peppol:bis:selfbilling", "customization_id": None}
	invoice_type_codes = {
		"invoice": codes.INVOICE_TYPE_TAX_INVOICE,
		"credit_note": codes.INVOICE_TYPE_CREDIT_NOTE,
		"debit_note": codes.INVOICE_TYPE_DEBIT_NOTE,
		"self_billed_invoice": codes.INVOICE_TYPE_SELF_BILLED_INVOICE,
		"self_billed_credit_note": codes.INVOICE_TYPE_SELF_BILLED_CREDIT_NOTE,
	}

	# ---- code tables ----------------------------------------------------
	tax_codes = ("S", "Z", "E", "AE", "O")  # UNCL5305 categories allowed
	tax_category_labels = {}  # lower-cased label / text / code -> code
	region_field = None  # Address field holding the subdivision, if the country has one
	region_codes = {}  # region name (lower-cased) -> subdivision code
	region_required = False  # throw when a domestic address has no recognised region
	uom_piece_code = "EA"

	# ---- generic helpers (rarely overridden) ----------------------------
	def money(self, value):
		return numbers.money(value, self.currency_decimals)

	qty = staticmethod(numbers.qty)
	num = staticmethod(numbers.num)
	dec = staticmethod(numbers.dec)
	split_code = staticmethod(codes.split_code)
	country_code_of = staticmethod(codes.country_code)

	@property
	def uom_table(self):
		return codes.uom_to_unece_table(self.uom_piece_code)

	def uom_to_unece(self, uom, default=None):
		return codes.uom_to_unece(uom, self.uom_table, default or self.uom_piece_code)

	def endpoint(self, participant_id):
		"""'0235:1234567890' -> ('1234567890', '0235'); bare id -> (id, default scheme)."""
		participant_id = (participant_id or "").strip()
		if ":" in participant_id:
			scheme, value = participant_id.split(":", 1)
			return value.strip(), scheme.strip()
		return participant_id, self.endpoint_scheme

	def endpoint_id_from_tax_id(self, tax_id):
		"""The participant identifier derived from the national tax ID.
		Default: the tax ID itself, digits only. Countries override."""
		return "".join(c for c in (tax_id or "") if c.isalnum())

	def tax_category_code(self, label):
		"""'S - Standard rated' -> 'S'. Accepts the full label, the plain text or
		the bare code; anything else throws."""
		if not label:
			return None
		key = label.strip().lower()
		code = self.tax_category_labels.get(key)
		if not code:
			code = label.split(" - ")[0].strip().upper()
		if code not in self.tax_codes:
			frappe.throw(
				_("Invalid Tax Category: {0}. Must be one of {1}.").format(label, ", ".join(self.tax_codes))
			)
		return code

	def region_code(self, region_name):
		if not region_name:
			return None
		return self.region_codes.get(region_name.strip().lower())

	def resolve_address_codes(self, address):
		"""ISO country code and subdivision for an Address doc.
		Throws when the country is unknown, or when a domestic address has no
		recognised region and the country requires one. Returns (country, subdivision)."""
		code = codes.country_code(address.get("country"))
		if not code:
			frappe.throw(_("Address {0} has no recognised Country.").format(address.name))

		subdivision = self.region_code(address.get(self.region_field)) if self.region_field else None
		if not subdivision:
			if code == self.country_code and self.region_required:
				frappe.throw(
					_("Address {0} needs a recognised {1} region ({2}).").format(
						address.name, self.country_name, ", ".join(self.region_names())
					)
				)
			subdivision = address.get("state") or address.get("city")
		return code, subdivision

	def region_names(self):
		return [n.title() for n in self.region_codes]

	def validate_document(self, doc):
		"""Country rules checked before an invoice is sent. Default: none."""
		return None

	def ui_labels(self):
		return {
			"country": self.country_code,
			"authority": self.authority_short,
			"authority_name": self.authority_name,
			"provider_label": self.provider_label,
			"provider_short": self.provider_short,
		}
