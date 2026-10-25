"""EN 16931 / Peppol BIS Billing 3.0 defaults (the European norm).

Used as-is by Belgium, the Netherlands, the Nordics and Ireland; Germany
(XRechnung) and France add national rules on top. Documents are UBL 2.1 with
the BIS customization ID and the generic ``urn:fdc:peppol.eu:2017:poacc:billing:01:1.0``
profile ID. Access points take the UBL XML itself (see core/standards/ubl),
not a provider JSON, which is the main practical difference from PINT markets.
"""

from einvoice_erpgulf.core.standards import BaseProfile


class EN16931Profile(BaseProfile):
	standard = "en16931"
	currency = "EUR"
	currency_decimals = 2
	uom_piece_code = "C62"  # "one" in UN/ECE Rec 20, the usual BIS choice for pieces

	process_billing = {
		"profile_id": "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0",
		"customization_id": "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0",
	}
	process_self_billing = {
		"profile_id": "urn:fdc:peppol.eu:2017:poacc:selfbilling:01:1.0",
		"customization_id": "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:selfbilling:3.0",
	}

	# UNCL5305 subset allowed by EN 16931
	tax_codes = ("S", "Z", "E", "AE", "K", "G", "O", "L", "M")
	tax_category_labels = {
		"s - standard rated": "S",
		"standard rated": "S",
		"standard": "S",
		"s": "S",
		"z - zero rated": "Z",
		"zero rated": "Z",
		"z": "Z",
		"e - exempt from tax": "E",
		"exempt from tax": "E",
		"exempt": "E",
		"e": "E",
		"ae - vat reverse charge": "AE",
		"vat reverse charge": "AE",
		"reverse charge": "AE",
		"ae": "AE",
		"k - intra-community supply": "K",
		"intra-community supply": "K",
		"intra-community": "K",
		"k": "K",
		"g - export outside the eu": "G",
		"export outside the eu": "G",
		"export": "G",
		"g": "G",
		"o - not subject to vat": "O",
		"not subject to vat": "O",
		"out of scope": "O",
		"o": "O",
		"l - canary islands general indirect tax": "L",
		"l": "L",
		"m - ceuta and melilla": "M",
		"m": "M",
	}

	# VATEX exemption reason codes most often needed; countries extend
	exemption_reason_codes = {
		"VATEX-EU-AE": "Reverse charge",
		"VATEX-EU-IC": "Intra-community supply",
		"VATEX-EU-G": "Export outside the EU",
		"VATEX-EU-O": "Not subject to VAT",
	}
