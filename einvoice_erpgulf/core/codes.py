"""Code tables that are international convention, not any country's choice:
ISO 3166 country codes, UN/ECE Rec 20 units, UNCL1001 document type codes,
and the small label parsers every adapter needs.
"""

# ---------------------------------------------------------------- UNCL1001 document type codes
INVOICE_TYPE_TAX_INVOICE = "380"
INVOICE_TYPE_CREDIT_NOTE = "381"
INVOICE_TYPE_DEBIT_NOTE = "383"
INVOICE_TYPE_SELF_BILLED_INVOICE = "389"
INVOICE_TYPE_SELF_BILLED_CREDIT_NOTE = "396"

# ---------------------------------------------------------------- UN/ECE Rec 20 units
# ERPNext UOM (lower-cased) -> UNECE Rec 20 code. "piece" style units map to
# `piece_code`, because providers differ on EA vs H87 (C62 in older BIS).
PIECE_UOMS = ("nos", "unit", "each", "pcs", "piece")
UOM_TO_UNECE_BASE = {
	"kg": "KGM",
	"kilogram": "KGM",
	"gram": "GRM",
	"gm": "GRM",
	"litre": "LTR",
	"liter": "LTR",
	"ltr": "LTR",
	"meter": "MTR",
	"metre": "MTR",
	"mtr": "MTR",
	"box": "BX",
	"pair": "PR",
	"dozen": "DZN",
	"hour": "HUR",
	"day": "DAY",
}


def uom_to_unece_table(piece_code="EA"):
	"""The UOM -> UNECE table with the chosen code for piece-type units."""
	table = {u: piece_code for u in PIECE_UOMS}
	table.update(UOM_TO_UNECE_BASE)
	return table


UOM_TO_UNECE = uom_to_unece_table("EA")


def uom_to_unece(uom, table=None, default="EA"):
	"""'Nos' -> 'EA'; unknown UOM -> its upper-cased name, empty -> default."""
	table = table or UOM_TO_UNECE
	if not uom:
		return default
	return table.get(uom.strip().lower(), uom.strip().upper() or default)


# ---------------------------------------------------------------- label parsing
def split_code(label):
	"""'DL8.61.1.A-Cancellation' / '30 - Credit transfer' -> (code, text).
	A label with no separator returns (label, label)."""
	raw = (label or "").strip()
	for sep in (" - ", "-"):
		if sep in raw:
			code, text = raw.split(sep, 1)
			return code.strip(), text.strip()
	return raw, raw


# ---------------------------------------------------------------- ISO 3166-1 alpha-2
COUNTRY_DATA = [
    {"name": "Afghanistan", "code": "AF"},
    {"name": "Albania", "code": "AL"},
    {"name": "Algeria", "code": "DZ"},
    {"name": "Andorra", "code": "AD"},
    {"name": "Angola", "code": "AO"},
    {"name": "Antigua and Barbuda", "code": "AG"},
    {"name": "Argentina", "code": "AR"},
    {"name": "Armenia", "code": "AM"},
    {"name": "Australia", "code": "AU"},
    {"name": "Austria", "code": "AT"},
    {"name": "Azerbaijan", "code": "AZ"},
    {"name": "Bahamas", "code": "BS"},
    {"name": "Bahrain", "code": "BH"},
    {"name": "Bangladesh", "code": "BD"},
    {"name": "Barbados", "code": "BB"},
    {"name": "Belarus", "code": "BY"},
    {"name": "Belgium", "code": "BE"},
    {"name": "Belize", "code": "BZ"},
    {"name": "Benin", "code": "BJ"},
    {"name": "Bhutan", "code": "BT"},
    {"name": "Bolivia", "code": "BO"},
    {"name": "Bosnia and Herzegovina", "code": "BA"},
    {"name": "Botswana", "code": "BW"},
    {"name": "Brazil", "code": "BR"},
    {"name": "Brunei", "code": "BN"},
    {"name": "Bulgaria", "code": "BG"},
    {"name": "Burkina Faso", "code": "BF"},
    {"name": "Burundi", "code": "BI"},
    {"name": "Cambodia", "code": "KH"},
    {"name": "Cameroon", "code": "CM"},
    {"name": "Canada", "code": "CA"},
    {"name": "Cape Verde", "code": "CV"},
    {"name": "Central African Republic", "code": "CF"},
    {"name": "Chad", "code": "TD"},
    {"name": "Chile", "code": "CL"},
    {"name": "China", "code": "CN"},
    {"name": "Colombia", "code": "CO"},
    {"name": "Comoros", "code": "KM"},
    {"name": "Congo (Congo-Brazzaville)", "code": "CG"},
    {"name": "Congo (DRC)", "code": "CD"},
    {"name": "Costa Rica", "code": "CR"},
    {"name": "Croatia", "code": "HR"},
    {"name": "Cuba", "code": "CU"},
    {"name": "Cyprus", "code": "CY"},
    {"name": "Czechia", "code": "CZ"},
    {"name": "Côte d'Ivoire", "code": "CI"},
    {"name": "Denmark", "code": "DK"},
    {"name": "Djibouti", "code": "DJ"},
    {"name": "Dominica", "code": "DM"},
    {"name": "Dominican Republic", "code": "DO"},
    {"name": "Ecuador", "code": "EC"},
    {"name": "Egypt", "code": "EG"},
    {"name": "El Salvador", "code": "SV"},
    {"name": "Equatorial Guinea", "code": "GQ"},
    {"name": "Eritrea", "code": "ER"},
    {"name": "Estonia", "code": "EE"},
    {"name": "Eswatini", "code": "SZ"},
    {"name": "Ethiopia", "code": "ET"},
    {"name": "Fiji", "code": "FJ"},
    {"name": "Finland", "code": "FI"},
    {"name": "France", "code": "FR"},
    {"name": "Ghana", "code": "GH"},
    {"name": "Greece", "code": "GR"},
    {"name": "Grenada", "code": "GD"},
    {"name": "Guatemala", "code": "GT"},
    {"name": "Guinea", "code": "GN"},
    {"name": "Guinea-Bissau", "code": "GW"},
    {"name": "Guyana", "code": "GY"},
    {"name": "Haiti", "code": "HT"},
    {"name": "Honduras", "code": "HN"},
    {"name": "Hungary", "code": "HU"},
    {"name": "Iceland", "code": "IS"},
    {"name": "India", "code": "IN"},
    {"name": "Indonesia", "code": "ID"},
    {"name": "Iran", "code": "IR"},
    {"name": "Iraq", "code": "IQ"},
    {"name": "Ireland", "code": "IE"},
    {"name": "Israel", "code": "IL"},
    {"name": "Italy", "code": "IT"},
    {"name": "Jamaica", "code": "JM"},
    {"name": "Japan", "code": "JP"},
    {"name": "Jordan", "code": "JO"},
    {"name": "Kazakhstan", "code": "KZ"},
    {"name": "Kenya", "code": "KE"},
    {"name": "Kiribati", "code": "KI"},
    {"name": "Kuwait", "code": "KW"},
    {"name": "Kyrgyzstan", "code": "KG"},
    {"name": "Laos", "code": "LA"},
    {"name": "Latvia", "code": "LV"},
    {"name": "Lebanon", "code": "LB"},
    {"name": "Lesotho", "code": "LS"},
    {"name": "Liberia", "code": "LR"},
    {"name": "Libya", "code": "LY"},
    {"name": "Liechtenstein", "code": "LI"},
    {"name": "Lithuania", "code": "LT"},
    {"name": "Luxembourg", "code": "LU"},
    {"name": "Madagascar", "code": "MG"},
    {"name": "Malawi", "code": "MW"},
    {"name": "Malaysia", "code": "MY"},
    {"name": "Maldives", "code": "MV"},
    {"name": "Mali", "code": "ML"},
    {"name": "Malta", "code": "MT"},
    {"name": "Marshall Islands", "code": "MH"},
    {"name": "Mauritania", "code": "MR"},
    {"name": "Mauritius", "code": "MU"},
    {"name": "Mexico", "code": "MX"},
    {"name": "Micronesia", "code": "FM"},
    {"name": "Moldova", "code": "MD"},
    {"name": "Monaco", "code": "MC"},
    {"name": "Mongolia", "code": "MN"},
    {"name": "Montenegro", "code": "ME"},
    {"name": "Morocco", "code": "MA"},
    {"name": "Mozambique", "code": "MZ"},
    {"name": "Myanmar", "code": "MM"},
    {"name": "Namibia", "code": "NA"},
    {"name": "Nauru", "code": "NR"},
    {"name": "Nepal", "code": "NP"},
    {"name": "Netherlands", "code": "NL"},
    {"name": "New Zealand", "code": "NZ"},
    {"name": "Nicaragua", "code": "NI"},
    {"name": "Niger", "code": "NE"},
    {"name": "Nigeria", "code": "NG"},
    {"name": "North Korea", "code": "KP"},
    {"name": "North Macedonia", "code": "MK"},
    {"name": "Norway", "code": "NO"},
    {"name": "Oman", "code": "OM"},
    {"name": "Pakistan", "code": "PK"},
    {"name": "Palau", "code": "PW"},
    {"name": "Palestine", "code": "PS"},
    {"name": "Panama", "code": "PA"},
    {"name": "Papua New Guinea", "code": "PG"},
    {"name": "Paraguay", "code": "PY"},
    {"name": "Peru", "code": "PE"},
    {"name": "Philippines", "code": "PH"},
    {"name": "Poland", "code": "PL"},
    {"name": "Portugal", "code": "PT"},
    {"name": "Qatar", "code": "QA"},
    {"name": "Romania", "code": "RO"},
    {"name": "Russia", "code": "RU"},
    {"name": "Rwanda", "code": "RW"},
    {"name": "Saint Kitts and Nevis", "code": "KN"},
    {"name": "Saint Lucia", "code": "LC"},
    {"name": "Saint Vincent and the Grenadines", "code": "VC"},
    {"name": "Samoa", "code": "WS"},
    {"name": "San Marino", "code": "SM"},
    {"name": "Sao Tome and Principe", "code": "ST"},
    {"name": "Saudi Arabia", "code": "SA"},
    {"name": "Senegal", "code": "SN"},
    {"name": "Serbia", "code": "RS"},
    {"name": "Seychelles", "code": "SC"},
    {"name": "Sierra Leone", "code": "SL"},
    {"name": "Singapore", "code": "SG"},
    {"name": "Slovakia", "code": "SK"},
    {"name": "Slovenia", "code": "SI"},
    {"name": "Solomon Islands", "code": "SB"},
    {"name": "Somalia", "code": "SO"},
    {"name": "South Africa", "code": "ZA"},
    {"name": "South Korea", "code": "KR"},
    {"name": "South Sudan", "code": "SS"},
    {"name": "Spain", "code": "ES"},
    {"name": "Sri Lanka", "code": "LK"},
    {"name": "Sudan", "code": "SD"},
    {"name": "Suriname", "code": "SR"},
    {"name": "Sweden", "code": "SE"},
    {"name": "Switzerland", "code": "CH"},
    {"name": "Syria", "code": "SY"},
    {"name": "Taiwan", "code": "TW"},
    {"name": "Tajikistan", "code": "TJ"},
    {"name": "Tanzania", "code": "TZ"},
    {"name": "Thailand", "code": "TH"},
    {"name": "Timor-Leste", "code": "TL"},
    {"name": "Togo", "code": "TG"},
    {"name": "Tonga", "code": "TO"},
    {"name": "Trinidad and Tobago", "code": "TT"},
    {"name": "Tunisia", "code": "TN"},
    {"name": "Turkey", "code": "TR"},
    {"name": "Turkmenistan", "code": "TM"},
    {"name": "Tuvalu", "code": "TV"},
    {"name": "Uganda", "code": "UG"},
    {"name": "Ukraine", "code": "UA"},
    {"name": "United Arab Emirates", "code": "AE"},
    {"name": "United Kingdom", "code": "GB"},
    {"name": "United States", "code": "US"},
    {"name": "Uruguay", "code": "UY"},
    {"name": "Uzbekistan", "code": "UZ"},
    {"name": "Vanuatu", "code": "VU"},
    {"name": "Vatican City", "code": "VA"},
    {"name": "Venezuela", "code": "VE"},
    {"name": "Vietnam", "code": "VN"},
    {"name": "Yemen", "code": "YE"},
    {"name": "Zambia", "code": "ZM"},
    {"name": "Zimbabwe", "code": "ZW"},
]

_COUNTRY_CODES = {entry["name"].lower(): entry["code"] for entry in COUNTRY_DATA}


def country_code_mapping():
	"""Country name (lower-cased) -> ISO 3166-1 alpha-2 code."""
	return _COUNTRY_CODES


def country_code(country_name):
	"""'United Arab Emirates' -> 'AE'; unknown or empty -> None."""
	if not country_name:
		return None
	return _COUNTRY_CODES.get(country_name.strip().lower())
