"""Belgium: Peppol BIS Billing 3.0 (EN 16931), mandatory B2B since 1 January 2026.

No national extension to the document, no accreditation gate (any certified
Peppol access point), no fifth corner until the 2028 reporting phase. The
participant identifier is the enterprise number (KBO/BCE, 10 digits) under
scheme 0208; the VAT number is "BE" + the same 10 digits. VAT is 21 / 12 / 6 %.

Access points in Belgium take the UBL document itself: an adapter for one
builds it with ``self.build_ubl(doc)`` (core/standards/ubl) and posts the XML.
"""

from einvoice_erpgulf.core.standards.en16931.profile import EN16931Profile


class BeProfile(EN16931Profile):
	country_code = "BE"
	country_name = "Belgium"
	authority_name = "FPS Finance"
	authority_short = "FPS Finance"
	provider_label = "Peppol Access Point"
	provider_short = "Access Point"

	currency = "EUR"
	currency_decimals = 2
	endpoint_scheme = "0208"  # KBO/BCE enterprise number

	region_field = None  # Belgium has no subdivision requirement
	region_required = False

	# A Belgian adapter's status flow ends when the buyer's access point
	# has the document ("delivered"); there is no clearance result to wait
	# for until the 2028 reporting phase.
	final_reporting_statuses = ("delivered", "reported", "rejected", "failed")

	def endpoint_id_from_tax_id(self, tax_id):
		"""'BE0123456789' / '0123.456.789' -> '0123456789' (10 digits, leading zero kept)."""
		digits = "".join(c for c in (tax_id or "") if c.isdigit())
		if len(digits) == 9:
			digits = "0" + digits
		return digits[:10]

	def validate_document(self, doc):
		"""BIS 3.0 needs a buyer reference or an order reference (BR-S-02 style rule
		used by most Belgian access points)."""
		import frappe
		from frappe import _

		if not (doc.get("po_no") or doc.get("custom_buyer_reference")):
			frappe.throw(
				_("Belgian e-invoices need a Buyer Reference or a Purchase Order number (Customer's PO No) on {0}.").format(
					doc.name
				)
			)
