"""Flick Network L.L.C adapter"""

import json
import frappe
import requests
from frappe import _
from frappe.utils import now_datetime
from einvoice_erpgulf.core.base import BaseAdapter
from einvoice_erpgulf.countries.ae.providers.flick import purchase_json, sales_json


def to_flick_sandbox_schema(doc_json):
    """Convert the new (pre-prod/production) JSON back to the old sandbox shape."""
    d = json.loads(json.dumps(doc_json))

    # flags -> metadata
    if "flags" in d:
        flags = d.pop("flags") or {}
        flags.pop("is_selfbilled", None)
        d["metadata"] = flags

    # identifiers map -> list
    party = d.get("receiving_party") or {}
    ids = party.get("identifiers")
    if isinstance(ids, dict):
        id_list = []
        for scheme, entry in ids.items():
            code = scheme.replace("AE:", "")
            if code == "VAT":
                party.setdefault("vat_number", entry.get("value"))
                continue
            row = {"type": code, "value": entry.get("value")}
            if entry.get("authority_name"):
                row["authority_name"] = entry["authority_name"]
            id_list.append(row)
        if id_list:
            party["identifiers"] = id_list
        else:
            party.pop("identifiers", None)

    # payment_means: type_code -> payment_means_code
    for pm in d.get("payment_means") or []:
        if isinstance(pm, dict) and "type_code" in pm:
            pm["payment_means_code"] = pm.pop("type_code")

    # invoice lines
    for line in d.get("invoice_lines") or []:
        if "quantity" in line:
            line["invoiced_quantity"] = line.pop("quantity")
        if "tax_category" in line:
            line["vat_category"] = line.pop("tax_category")
        if "tax_percentage" in line:
            line["vat_percentage"] = line.pop("tax_percentage")
        if "tax_exemption_reason_code" in line:
            line["vat_exemption_reason_code"] = line.pop("tax_exemption_reason_code")

    return d



class FlickAdapter(BaseAdapter):

    def parse_submit_response(self, status_code, response_data):
        """Flick answers 202 (accepted) with data.document_id; sandbox uses data.id."""
        body = response_data if isinstance(response_data, dict) else {}
        data = body.get("data") if isinstance(body.get("data"), dict) else {}

        document_id = data.get("document_id") or data.get("id") or body.get("id")
        reporting_status = data.get("reporting_status") or body.get("reporting_status")
        exchange_status = data.get("exchange_status") or body.get("exchange_status")

        success = status_code in (200, 201, 202) and (
            body.get("status") in ("success", "processed", "accepted") or bool(document_id)
        )

        return {
            "document_id": document_id,
            "reporting_status": reporting_status,
            "exchange_status": exchange_status,
            "success": success,
        }

    def _participant_url(self, suffix=""):
        """Sandbox -> {base}/v1/{participant_id}{suffix}
        Pre-Production / Production -> {base}/v1/participants/{participant_id}{suffix}"""
        base_url = (self.get_base_url() or "").rstrip("/")
        participant_id = self.settings.participant_id
        env = (self.settings.get("custom_environment") or "").strip().lower()
        if env == "sandbox":
            return f"{base_url}/v1/{participant_id}{suffix}"
        return f"{base_url}/v1/participants/{participant_id}{suffix}"

    def get_auth_headers(self, extra=None):
        headers = dict(extra or {})
        settings = self.settings

        if settings.auth_type == "api_key":
            api_key = settings.get_password("api_key")
            if not api_key:
                frappe.throw(
                    _("API Key is missing on E-Invoice Provider Settings {0}.").format(settings.name)
                )
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
        base_url = self.get_base_url()
        client_id = settings.client_id
        client_secret = settings.get_password("client_secret")
        if settings.auth_type == "api_key":
            frappe.throw(
                _("{0} uses API Key auth, so no access token is needed. "
                  "Use Get Participant Details or Verify Token to test.").format(settings.name),
                title=_("No Token for API Key"),
            )
        if not base_url or not client_id or not client_secret:
            frappe.throw(
                _("Please enter Base URL, Client ID and Client Secret on E-Invoice Provider Settings.")
            )

        url = f"{base_url}/v1/oauth/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload)
        response.raise_for_status()

        response_json = response.json()
        access_token = response_json.get("access_token")

        if not access_token:
            frappe.throw(_("Access token not found in response"))
        expires_in = response_json.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            self.set_cached_token(access_token, expires_in_sec=int(expires_in))
        else:
            self.set_cached_token(access_token)

        return access_token

    # ---- verify / participant ----
    def verify_auth(self):
        settings = self.settings
        base_url = self.get_base_url()

        if not base_url:
            frappe.throw(_("Please enter Base URL on E-Invoice Provider Settings."))

        try:
            headers = self.get_auth_headers()
            response = requests.get(f"{base_url}/v1/auth/verify", headers=headers)

            try:
                response_text = json.dumps(response.json())
            except Exception:
                response_text = response.text

            settings.db_set("last_test_status", "Passed" if response.ok else "Failed")
            settings.db_set("last_test_on", now_datetime())

            return {"status": "success" if response.ok else "error", "response": response_text}

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Flick Verify Error")
            settings.db_set("last_test_status", "Failed")
            settings.db_set("last_test_on", now_datetime())
            return {"status": "error", "message": str(e)}

    def get_participant_details(self):
        settings = self.settings
        base_url = self.get_base_url()

        if not base_url:
            frappe.throw(_("Please enter Base URL on E-Invoice Provider Settings."))

        participant_id = settings.participant_id
        if not participant_id:
            frappe.throw(_("Participant ID is missing on E-Invoice Provider Settings"))

        headers = self.get_auth_headers()
        url = f"{base_url}/v1/participants/{participant_id}"
        response = requests.get(url, headers=headers, timeout=60)

        try:
            body = response.json()
        except ValueError:
            body = response.text or _("(empty response body)")

        if not response.ok:
            return {
                "status": "error",
                "http_status": response.status_code,
                "url": url,
                "response": body,
            }

        return {"status": "success", "http_status": response.status_code, "response": body}

    def lookup_peppol_id(self, peppol_id):
        base_url = self.get_base_url()
        url = f"{base_url}/v1/peppol/lookup/{peppol_id}"
        headers = self.get_auth_headers()

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()

        frappe.throw(f"API Error: {response.text}")

    def update_participant(self, company_doc):
        settings = self.settings
        base_url = self.get_base_url()
        participant_id = settings.participant_id

        url = f"{base_url}/v1/participants/{participant_id}"
        headers = self.get_auth_headers({"Content-Type": "application/json"})

        payload = {
            "trade_name": company_doc.company_name,
            "legal_name": company_doc.company_name,
            "peppol_id": company_doc.custom_peppol_id,
            "street_address": company_doc.custom_street_address,
            "additional_street_address": company_doc.custom_additional_street,
            "additional_address_lines": company_doc.custom_additional_address,
            "city_address": company_doc.custom_city,
            "postal_zone": company_doc.custom_postal_code,
            "emirates_code": company_doc.custom_emirate_code,
            "country_code": company_doc.custom_country_code or "AE",
            "identifiers": [{"scheme_id": "AE:TL", "value": company_doc.tax_id}],
            "contact_name": company_doc.custom_contact_name,
            "contact_telephone": company_doc.custom_contact_phone,
            "contact_email": company_doc.custom_contact_email,
            "fz_beneficiary_id": company_doc.custom_fz_id,
        }

        try:
            response = requests.put(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            frappe.log_error(frappe.get_traceback(), "Flick Participant Update Error")
            return {
                "status": "error",
                "message": str(e),
                "response": getattr(e.response, "text", ""),
            }
    
    def get_status_from_document_status(self, body):
        """New API: data.reporting.status; sandbox: data.reporting_status."""
        if not isinstance(body, dict):
            return None
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        reporting = data.get("reporting") if isinstance(data.get("reporting"), dict) else {}
        return reporting.get("status") or data.get("reporting_status") or data.get("status")

    # ---- invoices ----
    def submit_invoice(self, doctype, doc, json_data=None):
        """Builds Flick's PEPPOL-style JSON (and attaches it to the invoice as
        <invoice>_uae_invoice.json) right here, then sends it. json_data is
        ignored - kept only so the signature matches BaseAdapter."""
        settings = self.settings
        participant_id = settings.participant_id
        if not participant_id:
            frappe.throw(_("Participant ID is missing on E-Invoice Provider Settings"))

        builder = purchase_json if doctype == "Purchase Invoice" else sales_json
        json_data = builder.save_and_attach_invoice_json(doc.name)["json"]

        base_url = self.get_base_url()
        headers = self.get_auth_headers({"Content-Type": "application/json"})

       

        # if doctype == "Purchase Invoice":
        #     url = self._participant_url("/simulate/incoming")
        # else:
        #     url = self._participant_url("/documents")

        env = (settings.get("custom_environment") or "").strip().lower()

        if doctype == "Purchase Invoice":
            if env == "sandbox":
                url = self._participant_url("/simulate/incoming")
            else:
                # pre-prod / production: self-billed invoice (389) via the normal documents endpoint
                url = self._participant_url("/documents")
                flags = json_data.pop("metadata", None) or json_data.get("flags") or {}
                flags["is_selfbilled"] = True
                json_data["flags"] = flags
                if json_data.get("document_type") not in ("389", "261"):
                    json_data["document_type"] = "261" if doc.get("is_return") else "389"
        else:
            url = self._participant_url("/documents")

        env = (settings.get("custom_environment") or "").strip().lower()
        if env == "sandbox" and doctype != "Purchase Invoice":
            json_data = to_flick_sandbox_schema(json_data)
        payload = {"document": json_data}

        response = requests.post(url, headers=headers, json=payload, timeout=120)

        try:
            response_data = response.json()
        except ValueError:
            if response.ok:
                response_data = response.text if doctype != "Purchase Invoice" else {}
            else:
                response_data = {
                    "http_status": response.status_code,
                    "reason": response.reason,
                    "url": url,
                    "body": response.text or "(empty response body)",
                }

        return response.status_code, response_data
        # response = requests.post(url, headers=headers, json=payload, timeout=120)

        # try:
        #     response_data = response.json()
        # except Exception:
        #     response_data = response.text if doctype != "Purchase Invoice" else {}

        return response.status_code, response_data

    def get_document_status(self, doctype, doc):
        """Return shape: always {"http_status": <int>, "response": <body>},
        success or failure. If the primary call to documents/{id} returns an empty list"""
        settings = self.settings
        participant_id = settings.participant_id
        if not participant_id:
            frappe.throw(_("Participant ID is missing on E-Invoice Provider Settings"))

        if not doc.custom_submit_response:
            frappe.throw(_("Submit response not found in Invoice"))

        response_data = json.loads(doc.custom_submit_response)
        submit_data = response_data.get("data", {})
        document_id = submit_data.get("document_id") or submit_data.get("id") or doc.get("custom_document_id")
        document_identifier = submit_data.get("document_identifier") or doc.name
        if not document_id:
            frappe.throw(_("Document ID not found in submit response"))

        base_url = self.get_base_url()
        if not base_url:
            frappe.throw(_("Base URL is missing on E-Invoice Provider Settings"))

        headers = self.get_auth_headers()

        # Primary call - the same "documents/{id}" path this adapter has
        # always used.
        url = self._participant_url(f"/documents/{document_id}")
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            return {"http_status": response.status_code, "response": response.text}

        try:
            data = response.json()
        except Exception:
            return {"http_status": response.status_code, "response": response.text}

    
        if isinstance(data, list) and not data and document_identifier:
            alt_url = self._participant_url("/documents")
            alt_response = requests.get(
                alt_url,
                headers=headers,
                params={"document_identifier": document_identifier},
            )
            if alt_response.status_code == 200:
                try:
                    alt_data = alt_response.json()
                except Exception:
                    alt_data = None
                if alt_data:
                    return {"http_status": alt_response.status_code, "response": alt_data}

        return {"http_status": response.status_code, "response": data}

    
    def get_document_xml(self, doctype, doc):
        settings = self.settings
        participant_id = settings.participant_id
        if not participant_id:
            frappe.throw(_("Participant ID is missing on E-Invoice Provider Settings"))

        data = {}
        if doc.custom_submit_response:
            data = json.loads(doc.custom_submit_response).get("data", {}) or {}
        document_id = data.get("document_id") or data.get("id") or doc.get("custom_document_id")
        if not document_id:
            frappe.throw(_("Document ID not found in submit response"))

        url = self._participant_url(f"/documents/{document_id}/xml")
        headers = self.get_auth_headers()
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            return response.text

        frappe.throw(_("API Error: {0}").format(response.text))

    def get_document_pdf(self, doctype, doc):
        settings = self.settings
        participant_id = settings.participant_id
        if not participant_id:
            frappe.throw(_("Participant ID is missing on E-Invoice Provider Settings"))

        data = {}
        if doc.custom_submit_response:
            data = json.loads(doc.custom_submit_response).get("data", {}) or {}
        document_id = data.get("document_id") or data.get("id") or doc.get("custom_document_id")
        if not document_id:
            frappe.throw(_("Document ID not found in submit response"))

        url = self._participant_url(f"/documents/{document_id}/pdf")
        headers = self.get_auth_headers()
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            return response.content

        frappe.throw(_("API Error: {0}").format(response.text))

    # ---- webhook ----
    def get_webhook_listener_url(self):
        """URL for flick_webhook_listener below - the one function in this
        file that genuinely can't be generic (it parses Flick's own webhook
        JSON shape directly), used both by register_webhook() here and by
        e_invoice_provider_settings.py's set_webhook_url() to fill in the
        Webhook URL field without that shared file needing to know Flick's
        URL itself."""
        return frappe.utils.get_url(
            "/api/method/einvoice_erpgulf.countries.ae.providers.flick.adapter.flick_webhook_listener"
        )

    def register_webhook(self):
        settings = self.settings
        base_url = self.get_base_url()
        url = f"{base_url}/v1/webhooks/subscriptions"
        participant_id = settings.participant_id

        headers = self.get_auth_headers({"Content-Type": "application/json"})

        endpoint = self.get_webhook_listener_url()
        payload = {
            "name": "ERPNext Webhook",
            "endpoint": endpoint,
            "event_types": [
                "document.received",
                "document.exchange.delivered",
                "document.exchange.failed",
                "document.reporting.reported",
                "document.reporting.failed",
                "document.completed",
                "document.failed",
            ],
            "participant_ids": [participant_id],
        }

        response = requests.post(url, headers=headers, json=payload)

        try:
            response_data = response.json()
        except Exception:
            response_data = {"raw_response": response.text}

        if response_data.get("data") and response_data["data"].get("uuid"):
            settings.webhook_uuid = response_data["data"]["uuid"]
        if response_data.get("data") and response_data["data"].get("secret"):
            settings.webhook_secret = response_data["data"]["secret"]
        settings.save(ignore_permissions=True)

        return response_data


    def get_subscription(self):
        settings = self.settings
        base_url = self.get_base_url()
        uuid = settings.webhook_uuid

        if not uuid:
            frappe.throw(_("Webhook UUID not found. Please create subscription first."))

        url = f"{base_url}/v1/webhooks/subscriptions/{uuid}"
        headers = self.get_auth_headers()
        response = requests.get(url, headers=headers)

        try:
            return response.json()
        except Exception:
            return {"raw_response": response.text}

    def delete_webhook(self, uuid=None):
        """DELETE /v1/webhooks/subscriptions/{uuid}. Defaults to this row's own subscription."""
        settings = self.settings
        uuid = uuid or settings.webhook_uuid
        if not uuid:
            frappe.throw(_("Webhook UUID not found."))

        url = f"{self.get_base_url()}/v1/webhooks/subscriptions/{uuid}"
        response = requests.delete(url, headers=self.get_auth_headers())

        try:
            data = response.json()
        except Exception:
            data = {"raw_response": response.text}

        if response.status_code in (200, 202, 204) and uuid == settings.webhook_uuid:
            settings.webhook_uuid = None
            settings.webhook_secret = None
            settings.save(ignore_permissions=True)

        return {"http_status": response.status_code, "response": data}
        
    def get_webhook_deliveries(self):
        settings = self.settings
        base_url = self.get_base_url()
        uuid = settings.webhook_uuid

        url = f"{base_url}/v1/webhooks/subscriptions/{uuid}/deliveries"
        headers = self.get_auth_headers()
        response = requests.get(url, headers=headers)

        try:
            return response.json()
        except Exception:
            return {"raw_response": response.text}

    # ---- incoming invoice import ----
    # def parse_incoming_invoice(self, invoice_json):
    #     """Flick's PEPPOL-style JSON: seller in receiving_party, lines in
    #     invoice_lines."""
    #     party = invoice_json.get("receiving_party") or {}

    #     lines = []
    #     for line in invoice_json.get("invoice_lines") or []:
    #         lines.append({
    #             "name": line.get("name"),
    #             "description": line.get("description"),
    #             "qty": float(line.get("invoiced_quantity") or 1),
    #             "uom": line.get("uom") or "Nos",
    #             "rate": float(line.get("unit_price") or 0),
    #             "amount": float(line.get("line_extension_amount") or 0),
    #             "vat_rate": float(line.get("vat_percentage") or 5),
    #         })

    #     payment_means_code = None
    #     payment_means = invoice_json.get("payment_means") or []
    #     if payment_means and isinstance(payment_means[0], dict):
    #         pm = payment_means[0]
    #         payment_means_code = pm.get("payment_means_code") or (
    #             pm.get("payment_means") or {}
    #         ).get("payment_means_code")

    #     return {
    #         "supplier_name": party.get("trade_name") or party.get("legal_name"),
    #         "vat_number": party.get("vat_number"),
    #         "posting_date": invoice_json.get("issue_date"),
    #         "due_date": invoice_json.get("due_date"),
    #         "currency": invoice_json.get("document_currency") or "AED",
    #         "document_id": invoice_json.get("document_identifier"),
    #         "conversion_rate": invoice_json.get("currency_exchange_rate"),
    #         "payment_means_code": payment_means_code,
    #         "lines": lines,
    #     }

        # e-invoice (UN/ECE) unit code -> ERPNext UOM
    UOM_FROM_CODE = {
        "H87": "Nos", "C62": "Nos", "EA": "Nos", "BX": "Box", "KGM": "Kg",
        "GRM": "Gram", "LTR": "Litre", "MTR": "Meter", "HUR": "Hour",
        "DAY": "Day", "MON": "Month",
    }

    def _erpnext_uom(self, code):
        """Map a UN/ECE unit code to an existing ERPNext UOM, else Nos."""
        code = (code or "").strip()
        if code and frappe.db.exists("UOM", code):
            return code
        uom = self.UOM_FROM_CODE.get(code.upper())
        if uom and frappe.db.exists("UOM", uom):
            return uom
        return "Nos"

    def parse_incoming_invoice(self, invoice_json):
        """Flick's PEPPOL-style JSON (old sandbox and new pre-prod/production keys)."""
        party = invoice_json.get("receiving_party") or {}

        lines = []
        for line in invoice_json.get("invoice_lines") or []:
            qty = line.get("quantity") or line.get("invoiced_quantity") or 1
            vat = line.get("tax_percentage")
            if vat is None:
                vat = line.get("vat_percentage")
            lines.append({
                "name": line.get("name"),
                "description": line.get("description") or line.get("name"),
                "qty": float(qty),
                "uom": self._erpnext_uom(line.get("uom")),
                "rate": float(line.get("unit_price") or 0),
                "amount": float(line.get("line_extension_amount") or 0),
                "vat_rate": float(vat if vat is not None else 5),
            })

        payment_means_code = None
        payment_means = invoice_json.get("payment_means") or []
        if payment_means and isinstance(payment_means[0], dict):
            pm = payment_means[0]
            payment_means_code = pm.get("type_code") or pm.get("payment_means_code") or (
                pm.get("payment_means") or {}
            ).get("payment_means_code")

        identifiers = party.get("identifiers") or {}
        vat_number = party.get("vat_number")
        if not vat_number and isinstance(identifiers, dict):
            vat_number = (identifiers.get("AE:VAT") or {}).get("value")

        flags = invoice_json.get("flags") or invoice_json.get("metadata") or {}

        return {
            "supplier_name": party.get("trade_name") or party.get("legal_name"),
            "vat_number": vat_number,
            "posting_date": invoice_json.get("issue_date"),
            "due_date": invoice_json.get("due_date"),
            "currency": invoice_json.get("document_currency") or "AED",
            "document_id": invoice_json.get("document_identifier"),
            "conversion_rate": invoice_json.get("currency_exchange_rate"),
            "payment_means_code": payment_means_code,
            "lines": lines,
        }
@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
def flick_webhook_listener():
    """Listener for Flick API webhooks. Logs incoming data and updates invoice status."""
    try:

        raw_data = frappe.request.get_data(as_text=True)
        data = json.loads(raw_data)

        # Extract top-level fields
        event_type = data.get("event")
        participant_id = data.get("participant_id")

        # Extract nested data
        doc_data = data.get("data", {})

        document_id = doc_data.get("document_id")
        status = doc_data.get("status")
        exchange_status = doc_data.get("exchange_status")
        reporting_status = doc_data.get("reporting_status")
        invoice_number = doc_data.get("document_identifier")

        # Create Webhook Log Doc
        doc = frappe.get_doc({
            "doctype": "E-Invoice Webhook Log",
            "webhook_response": raw_data,
            "document_id": document_id,
            "participant_id": participant_id,
            "event_type": event_type,
            "reporting_status": reporting_status,
            "exchange_status": exchange_status,
            "invoice_number": invoice_number,
            "status": status
        })

        doc.insert(ignore_permissions=True)
        if document_id and reporting_status:

            # Sales Invoice
            sales_invoice = frappe.db.get_value(
                "Sales Invoice",
                {"custom_document_id": document_id},
                "name"
            )

            if sales_invoice:
                frappe.db.set_value(
                    "Sales Invoice",
                    sales_invoice,
                    "custom_reporting_status",
                    reporting_status
                )

            # Purchase Invoice
            purchase_invoice = frappe.db.get_value(
                "Purchase Invoice",
                {"custom_document_id": document_id},
                "name"
            )

            if purchase_invoice:
                frappe.db.set_value(
                    "Purchase Invoice",
                    purchase_invoice,
                    "custom_reporting_status",
                    reporting_status
                )

        frappe.db.commit()  # nosemgrep: frappe-manual-commit

        return {
            "acknowledged": True,
            "processed": True
        }

    except Exception:
        frappe.log_error(
            title="Webhook Processing Error",
            message=frappe.get_traceback()
        )
        return {
            "acknowledged": False,
            "processed": False
        }