"""Permission helpers for every whitelisted e-invoice method."""
import frappe
from frappe import _

INVOICE_DOCTYPES = ("Sales Invoice", "Purchase Invoice")


def load_invoice(doctype, name, ptype="read"):
	if doctype not in INVOICE_DOCTYPES:
		frappe.throw(_("Not an invoice doctype: {0}").format(doctype), frappe.PermissionError)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission(ptype)
	return doc


def load_settings(company=None, provider_settings=None):
	"""Settings row for a Provider Settings button; System Manager only."""
	frappe.only_for("System Manager")
	from einvoice_erpgulf.core.provider_settings import get_settings_for_action

	settings = get_settings_for_action(company, provider_settings)
	settings.check_permission("write")
	return settings