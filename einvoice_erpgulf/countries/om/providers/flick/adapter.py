"""Flick Network L.L.C adapter - Oman (PINT-OM).

API reference: https://developer.flick.network/api-references/regional/om
Sandbox base URL: https://sb-om-api.flick.network

Every participant-scoped call is {base}/v1/{participant_id}/...
Auth: X-Flick-Auth-Key (static API key) or OAuth2 client credentials.
Never import from countries/ae/.
"""

import json

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime

from einvoice_erpgulf.core.base import BaseAdapter
from einvoice_erpgulf.countries.om.providers.flick import invoice_json
from einvoice_erpgulf.countries.om.providers.flick.invoice_json import (
    country_code,
    format_peppol_id,
)

TIMEOUT = 60
SUBMIT_TIMEOUT = 120
WEBHOOK_EVENTS = [
    "document.received",
    "document.exchange.delivered",
    "document.exchange.failed",
    "document.reporting.reported",
    "document.reporting.failed",
    "document.completed",
    "document.failed",
]
UOM_FROM_CODE = {
    "H87": "Nos", "C62": "Nos", "EA": "Nos", "BX": "Box", "KGM": "Kg",
    "GRM": "Gram", "LTR": "Litre", "MTR": "Meter", "HUR": "Hour",
    "DAY": "Day", "MON": "Month",
}


def _body(response):
    try:
        return response.json()
    except ValueError:
        return response.text or _("(empty response body)")


class FlickAdapter(BaseAdapter):

    # ---- urls / auth ----
    def _base(self):
        base_url = self.get_base_url()
        if not base_url:
            frappe.throw(_("Please enter Base URL on E-Invoice Provider Settings."))
        return base_url

    def _participant_id(self):
        participant_id = self.settings.participant_id
        if not participant_id:
            frappe.throw(_("Participant ID is missing on E-Invoice Provider Settings"))
        return participant_id

    def _participant_url(self, suffix=""):
        return f"{self._base()}/v1/{self._participant_id()}{suffix}"

    def get_auth_headers(self, extra=None):
        headers = dict(extra or {})
        settings = self.settings
        if settings.auth_type == "api_key":
            api_key = settings.get_password("api_key")
            if not api_key:
                frappe.throw(_("API Key is missing on E-Invoice Provider Settings {0}.").format(settings.name))
            headers["X-Flick-Auth-Key"] = api_key
        elif settings.auth_type == "oauth2":
            headers["Authorization"] = f"Bearer {self.get_valid_token()}"
        else:
            frappe.throw(
                _("Unsupported Auth Type '{0}' on E-Invoice Provider Settings {1}.").format(
                    settings.auth_type, settings.name
                )
            )
        return headers

    def _fetch_token(self):
        settings = self.settings
        if settings.auth_type == "api_key":
            frappe.throw(
                _("{0} uses API Key auth, so no access token is needed. Use Verify Token to test.").format(settings.name),
                title=_("No Token for API Key"),
            )
        client_id = settings.client_id
        client_secret = settings.get_password("client_secret")
        if not client_id or not client_secret:
            frappe.throw(_("Please enter Client ID and Client Secret on E-Invoice Provider Settings."))

        response = requests.post(
            f"{self._base()}/v1/oauth/token",
            json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("access_token") or (data.get("data") or {}).get("access_token")
        if not token:
            frappe.throw(_("Access token not found in response"))
        expires_in = data.get("expires_in") or (data.get("data") or {}).get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 60:
            self.set_cached_token(token, expires_in_sec=int(expires_in) - 30)
        else:
            self.set_cached_token(token)
        return token

    # ---- verify / participant ----
    def verify_auth(self):
        settings = self.settings
        try:
            response = requests.get(f"{self._base()}/v1/auth/verify", headers=self.get_auth_headers(), timeout=TIMEOUT)
            settings.db_set("last_test_status", "Passed" if response.ok else "Failed")
            settings.db_set("last_test_on", now_datetime())
            return {"status": "success" if response.ok else "error", "response": json.dumps(_body(response))}
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Flick Oman Verify Error")
            settings.db_set("last_test_status", "Failed")
            settings.db_set("last_test_on", now_datetime())
            return {"status": "error", "message": str(e)}

    def get_participant_details(self):
        url = f"{self._base()}/v1/participants/{self._participant_id()}"
        response = requests.get(url, headers=self.get_auth_headers(), timeout=TIMEOUT)
        result = {"status": "success" if response.ok else "error", "http_status": response.status_code, "response": _body(response)}
        if not response.ok:
            result["url"] = url
        return result

    def lookup_peppol_id(self, peppol_id):
        response = requests.get(
            f"{self._base()}/v1/peppol/lookup/{peppol_id}", headers=self.get_auth_headers(), timeout=TIMEOUT
        )
        if response.ok:
            return response.json()
        frappe.throw(_("API Error: {0}").format(response.text))

    def _participant_payload(self, company_doc):
        from erpnext.setup.doctype.company.company import get_default_company_address

        address_name = get_default_company_address(company_doc.name)
        if not address_name:
            frappe.throw(_("Set a default (Is Your Company Address) Address for {0}.").format(company_doc.name))
        address = frappe.get_doc("Address", address_name)

        identifiers = []
        if company_doc.get("custom_trade_license_number"):
            identifiers.append({"scheme_id": "OM:TL", "value": company_doc.custom_trade_license_number})
        if company_doc.tax_id:
            identifiers.append({"scheme_id": "OM:VAT", "value": company_doc.tax_id})

        payload = {
            "trade_name": company_doc.company_name,
            "legal_name": company_doc.company_name,
            "peppol_id": format_peppol_id(company_doc.get("custom_peppol_id") or company_doc.tax_id, _("Company")),
            "street_address": address.address_line1,
            "additional_street_address": address.address_line2,
            "city_address": address.city,
            "postal_zone": address.pincode,
            "country_code": country_code(address.country),
            "identifiers": identifiers,
            "contact_name": company_doc.company_name,
            "contact_telephone": address.phone,
            "contact_email": address.email_id or company_doc.email,
        }
        zone = (address.get("custom_om_special_zone") or "").strip()
        if zone:
            payload["country_subdivision_code"] = zone
        return {k: v for k, v in payload.items() if v not in (None, "", [])}

    def register_participant(self, company_doc):
        """POST /v1/participants - sandbox onboarding. Stores the returned id
        as this row's Participant ID."""
        response = requests.post(
            f"{self._base()}/v1/participants",
            headers=self.get_auth_headers({"Content-Type": "application/json"}),
            json=self._participant_payload(company_doc),
            timeout=TIMEOUT,
        )
        body = _body(response)
        new_id = body.get("data", {}).get("id") if isinstance(body, dict) else None
        if response.ok and new_id:
            self.settings.db_set("participant_id", new_id)
        return {"http_status": response.status_code, "response": body}

    def activate_participant(self, payload=None):
        response = requests.post(
            self._participant_url_admin("/activate"),
            headers=self.get_auth_headers({"Content-Type": "application/json"}),
            json=payload or {},
            timeout=TIMEOUT,
        )
        return {"http_status": response.status_code, "response": _body(response)}

    def _participant_url_admin(self, suffix=""):
        return f"{self._base()}/v1/participants/{self._participant_id()}{suffix}"

    def update_participant(self, company_doc):
        try:
            response = requests.put(
                self._participant_url_admin(),
                headers=self.get_auth_headers({"Content-Type": "application/json"}),
                json=self._participant_payload(company_doc),
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            frappe.log_error(frappe.get_traceback(), "Flick Oman Participant Update Error")
            return {"status": "error", "message": str(e), "response": getattr(e.response, "text", "")}

    # ---- invoices ----
    def submit_invoice(self, doctype, doc, json_data=None):
        """Builds the PINT-OM JSON (attached as <invoice>_om_invoice.json)
        and sends it. Purchase Invoices go as self-billed 389 / 261."""
        json_data = invoice_json.save_and_attach_invoice_json(doctype, doc.name)["json"]
        url = self._participant_url("/documents")
        response = requests.post(
            url,
            headers=self.get_auth_headers({"Content-Type": "application/json"}),
            json={"document": json_data},
            timeout=SUBMIT_TIMEOUT,
        )
        try:
            response_data = response.json()
        except ValueError:
            response_data = {
                "http_status": response.status_code,
                "reason": response.reason,
                "url": url,
                "body": response.text or "(empty response body)",
            }
        return response.status_code, response_data

    def validate_invoice(self, doctype, doc):
        """POST /documents/validate - checks the payload without sending it."""
        json_data = invoice_json.build_invoice_json(doctype, doc.name)
        response = requests.post(
            self._participant_url("/documents/validate"),
            headers=self.get_auth_headers({"Content-Type": "application/json"}),
            json={"document": json_data},
            timeout=SUBMIT_TIMEOUT,
        )
        return {"http_status": response.status_code, "response": _body(response)}

    def simulate_incoming(self, document_type="380", issuing_party=None, document=None):
        """Sandbox only: create an incoming document for testing the
        'Get Incoming Invoices' import (fires document.received)."""
        payload = {"type": document_type}
        if issuing_party:
            payload["issuing_party"] = issuing_party
        if document:
            payload["document"] = document
        response = requests.post(
            self._participant_url("/simulate/incoming"),
            headers=self.get_auth_headers({"Content-Type": "application/json"}),
            json=payload,
            timeout=SUBMIT_TIMEOUT,
        )
        return {"http_status": response.status_code, "response": _body(response)}

    def parse_submit_response(self, status_code, response_data):
        body = response_data if isinstance(response_data, dict) else {}
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        document_id = data.get("document_id") or data.get("id")
        return {
            "document_id": document_id,
            "reporting_status": data.get("reporting_status"),
            "exchange_status": data.get("exchange_status"),
            "success": status_code in (200, 201, 202) and (body.get("status") == "success" or bool(document_id)),
        }

    def get_status_from_document_status(self, body):
        if not isinstance(body, dict):
            return None
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        return data.get("reporting_status") or data.get("status")

    def _document_id(self, doc):
        data = {}
        if doc.get("custom_submit_response"):
            try:
                data = json.loads(doc.custom_submit_response).get("data") or {}
            except (ValueError, AttributeError):
                data = {}
        document_id = data.get("document_id") or data.get("id") or doc.get("custom_document_id")
        if not document_id:
            frappe.throw(_("Document ID not found. Submit the invoice to Flick first."))
        return document_id

    def get_document_status(self, doctype, doc):
        url = self._participant_url(f"/documents/{self._document_id(doc)}")
        response = requests.get(url, headers=self.get_auth_headers(), timeout=TIMEOUT)
        return {"http_status": response.status_code, "response": _body(response)}

    def get_document_xml(self, doctype, doc):
        response = requests.get(
            self._participant_url(f"/documents/{self._document_id(doc)}/xml"),
            headers=self.get_auth_headers(),
            timeout=TIMEOUT,
        )
        if response.ok:
            return response.text
        frappe.throw(_("API Error: {0}").format(response.text))

    def get_document_pdf(self, doctype, doc):
        response = requests.get(
            self._participant_url(f"/documents/{self._document_id(doc)}/pdf"),
            headers=self.get_auth_headers(),
            timeout=TIMEOUT,
        )
        if response.ok:
            return response.content
        frappe.throw(_("API Error: {0}").format(response.text))

    def list_documents(self, **params):
        """GET /documents - e.g. direction='incoming', is_unread=True."""
        response = requests.get(
            self._participant_url("/documents"), headers=self.get_auth_headers(), params=params, timeout=TIMEOUT
        )
        return {"http_status": response.status_code, "response": _body(response)}

    # ---- webhook ----
    def get_webhook_listener_url(self):
        return frappe.utils.get_url(
            "/api/method/einvoice_erpgulf.countries.om.providers.flick.adapter.flick_om_webhook_listener"
        )

    def register_webhook(self):
        settings = self.settings
        response = requests.post(
            f"{self._base()}/v1/webhooks/subscriptions",
            headers=self.get_auth_headers({"Content-Type": "application/json"}),
            json={
                "name": "ERPNext Webhook (Oman)",
                "endpoint": self.get_webhook_listener_url(),
                "event_types": WEBHOOK_EVENTS,
                "participant_ids": [self._participant_id()],
            },
            timeout=TIMEOUT,
        )
        body = _body(response)
        data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else {}
        if data.get("uuid"):
            settings.webhook_uuid = data["uuid"]
        if data.get("secret"):
            settings.webhook_secret = data["secret"]
        settings.save(ignore_permissions=True)
        return body

    def _subscription_url(self, uuid=None, suffix=""):
        uuid = uuid or self.settings.webhook_uuid
        if not uuid:
            frappe.throw(_("Webhook UUID not found. Please create the subscription first."))
        return f"{self._base()}/v1/webhooks/subscriptions/{uuid}{suffix}"

    def get_subscription(self):
        return _body(requests.get(self._subscription_url(), headers=self.get_auth_headers(), timeout=TIMEOUT))

    def delete_webhook(self, uuid=None):
        settings = self.settings
        uuid = uuid or settings.webhook_uuid
        response = requests.delete(self._subscription_url(uuid), headers=self.get_auth_headers(), timeout=TIMEOUT)
        if response.status_code in (200, 202, 204) and uuid == settings.webhook_uuid:
            settings.webhook_uuid = None
            settings.webhook_secret = None
            settings.save(ignore_permissions=True)
        return {"http_status": response.status_code, "response": _body(response)}

    def get_webhook_deliveries(self):
        return _body(
            requests.get(self._subscription_url(suffix="/deliveries"), headers=self.get_auth_headers(), timeout=TIMEOUT)
        )

    # ---- incoming invoice import ----
    def _erpnext_uom(self, code):
        code = (code or "").strip()
        if code and frappe.db.exists("UOM", code):
            return code
        uom = UOM_FROM_CODE.get(code.upper())
        return uom if uom and frappe.db.exists("UOM", uom) else "Nos"

    def parse_incoming_invoice(self, invoice_json_data):
        """Incoming PINT-OM JSON: the seller is issuing_party (falls back to
        receiving_party for documents simulated without one)."""
        party = invoice_json_data.get("issuing_party") or invoice_json_data.get("receiving_party") or {}

        lines = []
        for line in invoice_json_data.get("invoice_lines") or []:
            vat = line.get("vat_percentage")
            lines.append({
                "name": line.get("name"),
                "description": line.get("description") or line.get("name"),
                "qty": float(line.get("quantity") or 1),
                "uom": self._erpnext_uom(line.get("uom")),
                "rate": float(line.get("unit_price") or 0),
                "amount": float(line.get("line_extension_amount") or 0),
                "vat_rate": float(vat if vat not in (None, "") else 5),
            })

        payment_means_code = None
        payment_means = invoice_json_data.get("payment_means") or []
        if payment_means and isinstance(payment_means[0], dict):
            payment_means_code = payment_means[0].get("payment_means_code")

        return {
            "supplier_name": party.get("trade_name") or party.get("legal_name"),
            "vat_number": party.get("vat_number"),
            "posting_date": invoice_json_data.get("issue_date"),
            "due_date": invoice_json_data.get("due_date"),
            "currency": invoice_json_data.get("document_currency") or "OMR",
            "document_id": invoice_json_data.get("document_identifier"),
            "conversion_rate": invoice_json_data.get("currency_exchange_rate"),
            "payment_means_code": payment_means_code,
            "lines": lines,
        }


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
def flick_om_webhook_listener():
    """Flick Oman webhooks: log every event and update the invoice's
    reporting status."""
    try:
        raw_data = frappe.request.get_data(as_text=True)
        data = json.loads(raw_data)
        doc_data = data.get("data") or {}

        document_id = doc_data.get("document_id") or doc_data.get("id")
        reporting_status = doc_data.get("reporting_status")

        frappe.get_doc({
            "doctype": "E-Invoice Webhook Log",
            "webhook_response": raw_data,
            "document_id": document_id,
            "participant_id": data.get("participant_id"),
            "event_type": data.get("event") or data.get("event_type"),
            "reporting_status": reporting_status,
            "exchange_status": doc_data.get("exchange_status"),
            "invoice_number": doc_data.get("document_identifier"),
            "status": doc_data.get("status"),
        }).insert(ignore_permissions=True)

        if document_id and reporting_status:
            for doctype in ("Sales Invoice", "Purchase Invoice"):
                name = frappe.db.get_value(doctype, {"custom_document_id": document_id}, "name")
                if name:
                    frappe.db.set_value(doctype, name, "custom_reporting_status", reporting_status)

        frappe.db.commit()  # nosemgrep: frappe-manual-commit
        return {"acknowledged": True, "processed": True}

    except Exception:
        frappe.log_error(title="Flick Oman Webhook Error", message=frappe.get_traceback())
        return {"acknowledged": False, "processed": False}
