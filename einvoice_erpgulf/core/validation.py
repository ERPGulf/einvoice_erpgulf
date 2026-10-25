"""Validations hooked on Company and on Sales / Purchase Invoice."""
import frappe
from frappe import _
from einvoice_erpgulf.core.registry import assert_provider_registered, get_profile_for_country

def validate_company_provider(doc, method=None):
    """Company.validate: the chosen provider must have an adapter registered
    in hooks.py for the company's country. Refuse here, on the Company form,
    rather than later on Provider Settings or at invoice submit."""
    provider = doc.get("custom_einvoice_provider")
    if not provider:
        return
    profile = get_profile_for_country(doc.get("country"))
    assert_provider_registered(provider, country_code=profile.country_code)


def block_cancel_if_reported(doc, method=None):
    """before_cancel on Sales / Purchase Invoice: an invoice that reached the
    tax authority (or is on its way) cannot be cancelled in ERPNext; the only valid
    reversal is a credit / debit note that is itself reported."""
    reporting = (doc.get("custom_reporting_status") or "").strip().lower()
    einvoice = (doc.get("custom_einvoice_status") or "").strip().lower()

    # Never accepted by the tax authority -> safe to cancel
    if reporting in ("failed", "rejected"):
        return
    if einvoice != "success" and reporting not in ("reported", "pending"):
        return

    frappe.throw(
        _(
            "This invoice has already been sent for e-invoicing "
            "(reporting status: {0}) and cannot be cancelled. "
            "Create a Credit Note (Return) against it instead."
        ).format(reporting or "awaiting"),
        title=_("Cancellation not allowed"),
    )


def validate_accredited_service_provider(doc, method=None):
    """Runs before a Sales/Purchase Invoice is submitted. """
    company_doc = frappe.get_doc("Company", doc.company)

    if not company_doc.custom_einvoice_enabled:
        return

    provider = company_doc.custom_einvoice_provider
    if not provider:
        frappe.throw(_(
            "Select an E-Invoice Provider on {0} before submitting e-invoices."
        ).format(doc.company))

    # Fail here, with a clear message, rather than deep inside submit.
    assert_provider_registered(provider, company=doc.company)

    # Country rules (profile.validate_document) before anything is sent.
    get_profile_for_country(company_doc.country).validate_document(doc)

    has_settings = frappe.db.exists(
        "E-Invoice Provider Settings",
        {"company": doc.company, "provider": provider, "enabled": 1},
    )
    if not has_settings:
        frappe.throw(_(
            "No enabled E-Invoice Provider Settings found for {0} under {1}. "
            "Set up its credentials before submitting e-invoices."
        ).format(provider, doc.company))

  
def success_log(
    title=None,
    document_id=None,
    participant_id=None,
    invoice_number=None,
    reporting_status=None,
    exchange_status=None,
    status=None,
    submit_response=None,
):
    """Create E-Invoice Success Log"""

    try:
        log = frappe.get_doc(
            {
                "doctype": "E-Invoice Success Log",
                "title":  "E-Invoice Submitted Successfully",
                "document_id": document_id,
                "participant_id": participant_id,
                "invoice_number": invoice_number,
                "reporting_status": reporting_status,
                "exchange_status": exchange_status,
                "status": status,
                "submit_response": (
                    frappe.as_json(submit_response)
                    if isinstance(submit_response, (dict, list))
                    else submit_response
                ),
            }
        )

        log.insert(ignore_permissions=True)
        frappe.db.commit()

        return log.name

    except (
        ValueError,
        TypeError,
        KeyError,
        frappe.ValidationError,
    ) as e:
        frappe.log_error(
            title="E-Invoice Success Log Error",
            message=frappe.get_traceback(),
        )

        frappe.throw(_("Error in e-invoice success log: {0}").format(str(e)))