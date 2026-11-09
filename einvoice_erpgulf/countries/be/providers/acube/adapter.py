"""A-Cube Belgium (Peppol) access point.

Base URL: https://be-sandbox.api.acubeapi.com (sandbox)
          https://be.api.acubeapi.com         (production)
The generic peppol(-sandbox).api.acubeapi.com is a different product and
does not see entities made in the dashboard's Belgium context.

Login is repeated in the UAE adapter on purpose: a country folder never
imports from another country.

API: https://docs.acubeapi.com/documentation/gov-be/
"""

import json

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.file_manager import save_file
from lxml import etree

from einvoice_erpgulf.core.base import BaseAdapter

PROVIDER = "A-Cube"
LOGIN_URL = "https://common.api.acubeapi.com/login"
TOKEN_TTL_SEC = 23 * 3600  # A-Cube JWT lives 24 h
TIMEOUT = 30

# API paths (from https://docs.acubeapi.com/openapi/peppol-be-api.json)
INVOICES = "/invoices"
CREDIT_NOTES = "/credit-notes"
SEND_SUFFIX = "/outgoing/ubl"  # POST raw UBL XML -> 202
LEGAL_ENTITIES_PATH = "/legal-entities"
WEBHOOKS_PATH = "/webhooks"
WEBHOOK_EVENTS = ("outgoing-document", "incoming-document")

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def _json_or_text(r):
    try:
        return r.json()
    except Exception:
        return r.text


def _items(body):
    """A-Cube collections come as a plain list or JSON-LD (hydra:member)."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        return body.get("hydra:member") or body.get("member") or []
    return []


class ACubeAdapter(BaseAdapter):
    AUTO_FETCH_DOCUMENTS_ON_SUBMIT = False  # final only after delivery

    # ---------------------------------------------------------- auth
    def _environment(self):
        return "sandbox" if "-sandbox" in self.get_base_url() else "production"

    def _fetch_token(self):
        email = (self.settings.username or "").strip()
        password = (self.settings.get_password("password", raise_exception=False) or "").strip()
        if not email or not password:
            frappe.throw(_("Enter the A-Cube Username (email) and Password on E-Invoice Provider Settings."))

        r = requests.post(
            LOGIN_URL,
            json={"email": email, "password": password, "environment": self._environment()},
            timeout=TIMEOUT,
        )
        if not r.ok:
            frappe.throw(_("A-Cube login failed ({0}): {1}").format(r.status_code, r.text[:300]))

        token = (r.json() or {}).get("token")
        if not token:
            frappe.throw(_("A-Cube login returned no token."))
        self.set_cached_token(token, TOKEN_TTL_SEC)
        return token

    def get_auth_headers(self, extra=None):
        headers = {"Authorization": f"Bearer {self.get_valid_token()}", "Accept": "application/json"}
        headers.update(extra or {})
        return headers

    def _request(self, method, path, **kwargs):
        url = f"{self.get_base_url()}{path}"
        extra = kwargs.pop("headers", None)
        r = requests.request(method, url, headers=self.get_auth_headers(extra), timeout=TIMEOUT, **kwargs)
        if r.status_code == 401:  # token expired early: log in once more
            frappe.cache().delete_value(self._cache_key())
            r = requests.request(method, url, headers=self.get_auth_headers(extra), timeout=TIMEOUT, **kwargs)
        return r

    def verify_auth(self):
        """Log in, then check our Participant ID is a legal entity on the account."""
        ok, message = False, ""
        try:
            self._fetch_token()
            r = self._request("GET", LEGAL_ENTITIES_PATH)
            if not r.ok:
                message = f"Legal entities call failed ({r.status_code}): {r.text[:300]}"
            else:
                ours = (self.settings.participant_id or "").strip().split("::")[-1]
                found = [e for e in _items(_json_or_text(r))
                         if (e.get("identifierValue") or "").split("::")[-1] == ours]
                if not ours:
                    message = "Login OK, but Participant ID is empty."
                elif not found:
                    message = f"Login OK, but {ours} is not a legal entity on this A-Cube account."
                else:
                    ok = True
                    message = f"Login OK, legal entity {ours} found."
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "A-Cube Verify Error")
            message = str(e)

        self.settings.db_set("last_test_status", "Passed" if ok else "Failed")
        self.settings.db_set("last_test_on", now_datetime())
        return {"status": "success" if ok else "error", "message": message}

    def get_participant_details(self):
        r = self._request("GET", LEGAL_ENTITIES_PATH)
        ours = (self.settings.participant_id or "").strip().split("::")[-1]
        for e in _items(_json_or_text(r)):
            if (e.get("identifierValue") or "").split("::")[-1] == ours:
                return e
        return {"http_status": r.status_code, "response": _json_or_text(r)}

    # ---------------------------------------------------------- send
    @staticmethod
    def _type_path(doc_or_type):
        """/invoices or /credit-notes, from an invoice doc or a document_type string."""
        if isinstance(doc_or_type, str):
            return CREDIT_NOTES if "credit" in doc_or_type.lower() else INVOICES
        return CREDIT_NOTES if doc_or_type.get("is_return") else INVOICES

    def submit_invoice(self, doctype, doc, json_data=None):
        xml = self.build_ubl(doc)  # UBL 2.1 (Invoice or CreditNote) from BeProfile
        if isinstance(xml, str):
            xml = xml.encode("utf-8")

        # save_outgoing_payload writes JSON, so wrap the XML in a dict
        self.save_outgoing_payload(doc, {"content_type": "application/xml", "ubl_xml": xml.decode("utf-8")})

        path = self._type_path(doc) + SEND_SUFFIX
        r = self._request("POST", path, data=xml, headers={"Content-Type": "application/xml"})
        return r.status_code, _json_or_text(r)

    def parse_submit_response(self, status_code, response_data):
        """202 = passed A-Cube validation and queued for Peppol; body is the
        document resource with its uuid. 400 = validation error (JSON)."""
        body = response_data if isinstance(response_data, dict) else {}
        document_id = body.get("uuid") or body.get("id")
        success = status_code in (200, 201, 202) and bool(document_id)
        return {
            "document_id": document_id,
            # "sent" until the outgoing-document webhook (or sync) says delivered / failed
            "reporting_status": "sent" if success else "failed",
            "exchange_status": None,
            "success": success,
        }

    # ---------------------------------------------------------- status
    def _document_id(self, doc):
        document_id = doc.get("custom_document_id")
        if not document_id:
            frappe.throw(_("Document ID not found on {0}. Send it to A-Cube first.").format(doc.name))
        return document_id

    def get_document_status(self, doctype, doc):
        path = f"{self._type_path(doc)}/{self._document_id(doc)}"
        r = self._request("GET", path)
        return {"http_status": r.status_code, "response": _json_or_text(r)}

    def get_status_from_document_status(self, body):
        """Map A-Cube's document resource to our status.
        Confirm the field names from the first real response, which the sync
        stores in custom_document_status_response on the invoice."""
        if not isinstance(body, dict):
            return None
        if body.get("success") is True:
            return "delivered"
        if body.get("success") is False:
            return "failed"
        for key in ("status", "sendingStatus", "deliveryStatus"):
            value = body.get(key)
            if isinstance(value, str) and value:
                value = value.lower()
                if value in ("sent", "delivered", "success", "succeeded"):
                    return "delivered"
                if value in ("failed", "failure", "error", "rejected"):
                    return "failed"
                return value
        return None

    def get_document_xml(self, doctype, doc):
        r = self._request("GET", f"{self._type_path(doc)}/{self._document_id(doc)}/source",
                          headers={"Accept": "application/xml"})
        if r.ok:
            return r.text
        frappe.throw(_("A-Cube XML download failed ({0}): {1}").format(r.status_code, r.text[:300]))

    def get_document_pdf(self, doctype, doc):
        # /print answers 302 to the file; requests follows it and drops our
        # Authorization header if the file lives on another host
        r = self._request("GET", f"{self._type_path(doc)}/{self._document_id(doc)}/print",
                          headers={"Accept": "application/pdf"})
        if r.ok:
            return r.content
        frappe.throw(_("A-Cube PDF download failed ({0}): {1}").format(r.status_code, r.text[:300]))

    # ---------------------------------------------------------- webhooks
    def get_webhook_listener_url(self):
        return frappe.utils.get_url(
            "/api/method/einvoice_erpgulf.countries.be.providers.acube.adapter.acube_webhook_listener"
        )

    def register_webhook(self):
        """One webhook per event. The event goes in the URL because A-Cube's
        payload doesn't say which event it is."""
        base = self.get_webhook_listener_url()
        results = {}
        for event in WEBHOOK_EVENTS:
            r = self._request("POST", WEBHOOKS_PATH, json={"event": event, "target_url": f"{base}?event={event}"})
            results[event] = {"http_status": r.status_code, "response": _json_or_text(r)}

        first = results["outgoing-document"]["response"]
        if isinstance(first, dict) and (first.get("uuid") or first.get("id")):
            self.settings.db_set("webhook_uuid", first.get("uuid") or first.get("id"))
        return results

    def get_subscription(self):
        r = self._request("GET", WEBHOOKS_PATH)
        return {"http_status": r.status_code, "response": _json_or_text(r)}

    def get_webhook_deliveries(self):
        """A-Cube has no delivery-log endpoint; our listener writes E-Invoice
        Webhook Log itself. Returning quietly keeps the every-minute
        update_webhook_logs job from logging an error for this row."""
        return {"message": "A-Cube deliveries are logged by acube_webhook_listener."}

    def delete_webhook(self, uuid=None):
        """Without a uuid: delete every webhook of this account that points at us."""
        if uuid:
            uuids = [uuid]
        else:
            ours = self.get_webhook_listener_url()
            listed = _items(_json_or_text(self._request("GET", WEBHOOKS_PATH)))
            uuids = [w.get("uuid") or w.get("id") for w in listed if (w.get("target_url") or "").startswith(ours)]

        results = {}
        for u in uuids:
            r = self._request("DELETE", f"{WEBHOOKS_PATH}/{u}")
            results[u] = r.status_code
        self.settings.db_set("webhook_uuid", None)
        return results

    # ---------------------------------------------------------- incoming
    def fetch_incoming_xml(self, document_id, document_type="invoice"):
        r = self._request("GET", f"{self._type_path(document_type)}/{document_id}/source",
                          headers={"Accept": "application/xml"})
        if not r.ok:
            frappe.throw(_("A-Cube incoming document fetch failed ({0}): {1}").format(r.status_code, r.text[:300]))
        return r.text

    def parse_incoming_invoice(self, invoice_json):
        """The file we store for an A-Cube incoming document is
        {"acube_uuid", "document_type", "ubl_xml"}; read the UBL inside it."""
        xml = (invoice_json or {}).get("ubl_xml") if isinstance(invoice_json, dict) else None
        if not xml:
            return {}

        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(xml.encode("utf-8"), parser)

        def text(node, path):
            found = node.find(path, NS)
            return found.text.strip() if found is not None and found.text else None

        party = root.find("cac:AccountingSupplierParty/cac:Party", NS)
        supplier_name = vat_number = None
        if party is not None:
            supplier_name = (text(party, "cac:PartyLegalEntity/cbc:RegistrationName")
                             or text(party, "cac:PartyName/cbc:Name"))
            vat_number = text(party, "cac:PartyTaxScheme/cbc:CompanyID")

        lines = []
        for line in root.findall("cac:InvoiceLine", NS) + root.findall("cac:CreditNoteLine", NS):
            qty_node = line.find("cbc:InvoicedQuantity", NS)
            if qty_node is None:
                qty_node = line.find("cbc:CreditedQuantity", NS)
            unit = qty_node.get("unitCode") if qty_node is not None else None
            name = text(line, "cac:Item/cbc:Name")
            vat = text(line, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent")
            lines.append({
                "name": name,
                "description": text(line, "cac:Item/cbc:Description") or name,
                "qty": float(qty_node.text) if qty_node is not None and qty_node.text else 1.0,
                "uom": unit if unit and frappe.db.exists("UOM", unit) else "Nos",
                "rate": float(text(line, "cac:Price/cbc:PriceAmount") or 0),
                "amount": float(text(line, "cbc:LineExtensionAmount") or 0),
                "vat_rate": float(vat) if vat else 0.0,
            })

        return {
            "supplier_name": supplier_name,
            "vat_number": vat_number,
            "posting_date": text(root, "cbc:IssueDate"),
            "due_date": text(root, "cbc:DueDate") or text(root, "cac:PaymentMeans/cbc:PaymentDueDate"),
            "currency": text(root, "cbc:DocumentCurrencyCode") or "EUR",
            "document_id": invoice_json.get("acube_uuid") or text(root, "cbc:ID"),
            "conversion_rate": None,
            "payment_means_code": text(root, "cac:PaymentMeans/cbc:PaymentMeansCode"),
            "lines": lines,
        }


# ============================================================== webhook
@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
def acube_webhook_listener():
    """A-Cube calls this for outgoing-document and incoming-document.
    Log it, queue the work, answer 200 fast (non-2xx makes A-Cube retry
    for ~10 hours).

    TODO before production: verify A-Cube's HTTP Signature here and
    return 401 on failure. Until then an outgoing status is only applied to
    an invoice that already carries that document_id, and an incoming
    document is always re-read from A-Cube's own API, never from the body.
    """
    raw = frappe.request.get_data(as_text=True) or ""
    event = frappe.form_dict.get("event") or ""
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}

    try:
        frappe.get_doc({
            "doctype": "E-Invoice Webhook Log",
            "webhook_response": raw,
            "document_id": data.get("document_id"),
            "event_type": event,
            "status": "success" if data.get("success") else "failure",
            "reporting_status": data.get("error_code"),
        }).insert(ignore_permissions=True)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit
    except Exception:
        frappe.log_error(frappe.get_traceback(), "A-Cube Webhook Log Error")

    if data.get("document_id") and event in WEBHOOK_EVENTS:
        frappe.enqueue(
            "einvoice_erpgulf.countries.be.providers.acube.adapter.process_webhook",
            queue="short",
            event=event,
            data=data,
        )
    return {"acknowledged": True}


def process_webhook(event, data):
    try:
        if event == "outgoing-document":
            _apply_outgoing_status(data)
        elif event == "incoming-document":
            _import_incoming_document(data)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), f"A-Cube Webhook Processing Error - {event}")


def _apply_outgoing_status(data):
    document_id = data["document_id"]
    status = "delivered" if data.get("success") else "failed"
    for doctype in ("Sales Invoice", "Purchase Invoice"):
        name = frappe.db.get_value(doctype, {"custom_document_id": document_id}, "name")
        if name:
            frappe.db.set_value(doctype, name, {
                "custom_reporting_status": status,
                "custom_document_status_response": json.dumps(data),
            })


def _acube_settings_rows():
    return frappe.get_all(
        "E-Invoice Provider Settings",
        filters={"provider": PROVIDER, "enabled": 1},
        pluck="name",
    )


def _import_incoming_document(data):
    """Fetch the received UBL from A-Cube and store it as an E-Invoice
    Incoming Invoice; "Create Purchase Invoice" then works as for any ASP."""
    document_id = data["document_id"]
    if frappe.db.exists("E-Invoice Incoming Invoice", {"document_id": document_id}):
        return

    document_type = data.get("document_type") or "invoice"
    xml = None
    for row in _acube_settings_rows():
        settings = frappe.get_doc("E-Invoice Provider Settings", row)
        if "be" not in (settings.base_url_override or settings.base_url or "").split("//")[-1].split(".")[0]:
            continue  # only Belgian A-Cube rows (be / be-sandbox)
        try:
            xml = ACubeAdapter(settings).fetch_incoming_xml(document_id, document_type)
            break
        except Exception:
            continue  # not this account's document
    if not xml:
        frappe.throw(_("A-Cube incoming document {0} could not be fetched with any A-Cube settings row.").format(document_id))

    payload = {"acube_uuid": document_id, "document_type": document_type, "ubl_xml": xml}
    incoming = frappe.get_doc({
        "doctype": "E-Invoice Incoming Invoice",
        "document_id": document_id,
        "status": "Not Submitted",
        "submit_response": json.dumps(data),
    }).insert(ignore_permissions=True)

    file_doc = save_file(
        fname=f"acube-incoming-{document_id}.json",
        content=json.dumps(payload, indent=2),
        dt="E-Invoice Incoming Invoice",
        dn=incoming.name,
        is_private=1,
    )
    incoming.db_set("incoming_invoice_file", file_doc.file_url)