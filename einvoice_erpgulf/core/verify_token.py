"""Provider auth checks and document status."""
import json

import frappe
from frappe import _

from einvoice_erpgulf.core.permissions import load_invoice, load_settings
from einvoice_erpgulf.core.provider_settings import (
	get_active_provider_settings,
	save_last_response,
)
from einvoice_erpgulf.core.registry import get_adapter


def get_auth_headers(settings, extra=None):
	"""Kept for backward compatibility."""
	return get_adapter(settings).get_auth_headers(extra)


@frappe.whitelist()
def verify_auth(company: str = None, provider_settings: str = None):
	"""Verify button. Needs read permission on Provider Settings."""
	settings = load_settings(company, provider_settings, "read")
	result = get_adapter(settings).verify_auth()

	if isinstance(result, dict):
		save_last_response(settings, "last_test_response", result.get("response"))

	return result


@frappe.whitelist()
def get_participant_details(company: str = None, provider_settings: str = None):
	"""Get Participant Details button. Needs read permission on Provider Settings."""
	settings = load_settings(company, provider_settings, "read")
	result = get_adapter(settings).get_participant_details()

	if isinstance(result, dict):
		save_last_response(settings, "last_participant_response", result.get("response"))

	return result


def get_valid_token(company):
	"""Valid access token for this company's provider. Server-side only."""
	settings = get_active_provider_settings(company)
	return get_adapter(settings).get_valid_token()


# old Flick-named paths, kept so existing callers keep working
verify_flick_token = verify_auth
get_valid_flick_token = get_valid_token


def fetch_document_status(doc):
	"""Get and save the provider status of an invoice. No permission check:
	for the scheduler and webhooks only."""
	settings = get_active_provider_settings(doc.company)
	adapter = get_adapter(settings)
	result = adapter.get_document_status(doc.doctype, doc)

	if isinstance(result, dict) and "http_status" in result:
		http_status = result.get("http_status")
		body = result.get("response")
	else:
		http_status = None
		body = result

	doc.db_set("custom_document_status_response", json.dumps(body))

	reporting_status = result.get("reporting_status") if isinstance(result, dict) else None
	if not reporting_status:
		reporting_status = adapter.get_status_from_document_status(body)
	if reporting_status:
		doc.db_set("custom_reporting_status", reporting_status)

	return {"http_status": http_status, "response": body, "reporting_status": reporting_status}


@frappe.whitelist(methods=["POST"])
def get_document_status(invoice_name: str):
	"""Status button on Sales Invoice. The user must be able to open the invoice."""
	doc = load_invoice("Sales Invoice", invoice_name, "read")
	try:
		return fetch_document_status(doc)
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "E-Invoice Document Status Error")
		frappe.throw(_("Failed to fetch document status: {0}").format(str(e)))