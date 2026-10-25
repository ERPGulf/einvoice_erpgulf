"""Peppol PINT (Peppol International) defaults.

PINT is the globalised EN 16931: a country specialises it with its own
customization ID (``urn:peppol:pint:billing-1@<cc>-1``), tax categories,
identifier scheme and region codes. Countries using it: UAE (PINT-AE), Oman
(PINT-OM), Singapore (PINT-SG), Malaysia (PINT-MY), Australia / New Zealand
(PINT A-NZ), Japan (JP PINT).
"""

from einvoice_erpgulf.core.standards import BaseProfile


class PintProfile(BaseProfile):
	standard = "pint"
	pint_suffix = None  # e.g. "ae-1"; builds the customization IDs below

	# UNCL5305 categories PINT allows by default; countries narrow or extend
	tax_codes = ("S", "Z", "E", "AE", "O", "G", "K")
	tax_category_labels = {
		"s - standard rated": "S",
		"standard rated": "S",
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
		"o - not subject to vat": "O",
		"not subject to vat": "O",
		"out of scope": "O",
		"o": "O",
		"g - export": "G",
		"export": "G",
		"g": "G",
		"k - intra-community supply": "K",
		"k": "K",
	}

	@property
	def process_billing(self):
		return {
			"profile_id": "urn:peppol:bis:billing",
			"customization_id": f"urn:peppol:pint:billing-1@{self.pint_suffix}" if self.pint_suffix else "urn:peppol:pint:billing-1",
		}

	@property
	def process_self_billing(self):
		return {
			"profile_id": "urn:peppol:bis:selfbilling",
			"customization_id": f"urn:peppol:pint:selfbilling-1@{self.pint_suffix}" if self.pint_suffix else "urn:peppol:pint:selfbilling-1",
		}
