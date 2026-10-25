"""Oman: PINT-OM profile (Oman Tax Authority, Peppol 5-corner with accredited
service providers — the same model as the UAE).

STATUS: the four constants marked TODO below must be confirmed against the
Oman Tax Authority's published technical specification before the first
customer goes live. Everything else is inherited from PintProfile and is the
same as the UAE.

Providers: the first Oman ASPs are expected to be the same companies as in
the UAE. Register the existing adapter class a second time under "OM" in
hooks.py (einvoice_providers) — no new adapter code is needed unless the
provider's Oman API differs.
"""

from einvoice_erpgulf.core.standards.pint.profile import PintProfile

# Governorate (as on Address.custom_governorate) -> subdivision code (ISO 3166-2:OM)
GOVERNORATE_CODES = {
	"muscat": "MA",
	"dhofar": "ZU",
	"musandam": "MU",
	"al buraimi": "BU",
	"ad dakhiliyah": "DA",
	"al batinah north": "BS",
	"al batinah south": "BJ",
	"ash sharqiyah north": "SS",
	"ash sharqiyah south": "SJ",
	"ad dhahirah": "ZA",
	"al wusta": "WU",
}


class OmProfile(PintProfile):
	country_code = "OM"
	country_name = "Oman"
	authority_name = "Oman Tax Authority"
	authority_short = "OTA"
	provider_label = "Accredited Service Provider"
	provider_short = "ASP"

	currency = "OMR"
	currency_decimals = 3  # baisa

	# TODO confirm against the OTA specification:
	pint_suffix = "om-1"  # customization ID suffix -> urn:peppol:pint:billing-1@om-1
	endpoint_scheme = "0235"  # Peppol participant scheme for the Oman VATIN (0235 is the UAE's; OTA may register its own ICD)
	tax_id_length = 10  # digits of the VATIN used as the endpoint identifier
	region_required = False  # whether a domestic address must carry a governorate

	region_field = "custom_governorate"
	region_codes = GOVERNORATE_CODES

	def endpoint_id_from_tax_id(self, tax_id):
		digits = "".join(c for c in (tax_id or "") if c.isdigit())
		return digits[: self.tax_id_length] if self.tax_id_length else digits
