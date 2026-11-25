"""Webhook subscription buttons and the webhook-log scheduler."""
import frappe
from frappe import _

from einvoice_erpgulf.core.permissions import load_settings
from einvoice_erpgulf.core.provider_settings import save_last_response
from einvoice_erpgulf.core.registry import get_adapter


# ---- internal: no permission check (scheduler only) ----
def _deliveries(settings):
    result = get_adapter(settings).get_webhook_deliveries()
    save_last_response(settings, "last_webhook_logs_response", result)
    return result


def update_webhook_logs():
    """Scheduler: refresh the webhook delivery log of every enabled row."""
    rows = frappe.get_all(
        "E-Invoice Provider Settings",
        filters={"enabled": 1, "webhook_uuid": ["is", "set"]},
        fields=["name", "company"],
    )
    for row in rows:
        try:
            _deliveries(frappe.get_doc("E-Invoice Provider Settings", row.name))
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Webhook Log Update Failed - {row.company}")


# ---- buttons on E-Invoice Provider Settings ----
@frappe.whitelist()
def register_webhook(company: str = None, provider_settings: str = None):
    """Subscribe Webhook. Needs write permission."""
    settings = load_settings(company, provider_settings, "write")
    result = get_adapter(settings).register_webhook()
    save_last_response(settings, "last_webhook_subscribe_response", result)
    return result


@frappe.whitelist()
def custom_get_subscription(company: str = None, provider_settings: str = None):
    """Get Subscription. Needs read permission."""
    settings = load_settings(company, provider_settings, "read")
    result = get_adapter(settings).get_subscription()
    save_last_response(settings, "last_webhook_subscription_response", result)
    return result


@frappe.whitelist()
def delete_webhook(company: str = None, provider_settings: str = None, uuid: str = None):
    """Delete Webhook. Needs write permission."""
    settings = load_settings(company, provider_settings, "write")
    return get_adapter(settings).delete_webhook(uuid)


@frappe.whitelist()
def get_webhook_deliveries(company: str = None, provider_settings: str = None):
    """Webhook Logs. Needs read permission."""
    return _deliveries(load_settings(company, provider_settings, "read"))


# old Flick-named path, kept so anything still calling it keeps working
register_flick_webhook = register_webhook
