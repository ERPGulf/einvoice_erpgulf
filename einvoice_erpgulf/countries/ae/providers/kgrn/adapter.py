"""KGRN Chartered Accountants (KXample platform) - UAE Accredited Service Provider.

Base URL: https://uat.kgrnaudit.com   (UAT / sandbox)
          https://live.kgrnaudit.com  (production)

AR (outbound): POST /api/v2/external/outbound/invoice with the UAE PINT V2.0
JSON (kgrn_json.py). KGRN validates against PINT-AE, builds the UBL and
transmits it to the Access Point; the answer usually already says "cleared".

Auth (E-Invoice Provider Settings > Auth Type):
  api_key -> API Key field holds the per-entity Bearer token (kgrn_out_...)
  oauth2  -> Client ID / Client Secret, exchanged at
             /api/v2/external/outbound/oauth/token for a 1 h token

Documents: GET /api/v2/external/process/reports/<Report>
           ?referencenumber=<invoice name>&financialyear=<YYYY>

AP (incoming), both supported:
  push - KGRN posts the v2 invoice JSON to kgrn_ap_listener; ask KGRN to use
         the Bearer scheme with the Webhook Secret as the token.
  pull - GET /api/v2/external/ap/json?hours=N (pull_incoming_invoices).
"""

import hmac
import json

import frappe
import requests
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import flt, getdate, now_datetime
from frappe.utils.file_manager import save_file
from lxml import etree

from einvoice_erpgulf.core.base import BaseAdapter
from einvoice_erpgulf.countries.ae.providers.kgrn.kgrn_json import build_kgrn_payload

PROVIDER = "KGRN Chartered Accountants"
UAT_URL = "https://uat.kgrnaudit.com"
LIVE_URL = "https://live.kgrnaudit.com"
TIMEOUT = 60

OUTBOUND = "/api/v2/external/outbound/invoice"
TOKEN_PATH = "/api/v2/external/outbound/oauth/token"
WHOAMI_PATH = "/api/v2/external/outbound/whoami"
REPORTS_PATH = "/api/v2/external/process/reports/"
AP_PULL_PATH = "/api/v2/external/ap/json"
OAUTH_TTL_SEC = 55 * 60  # KGRN OAuth tokens live 1 h

REPORT_XML = "Invoice_XML_Report"
REPORT_PDF = "eInvoice-UAE-PDF"
REPORT_MLS = "Invoice_MLS_XML_Report"

# KGRN result status -> custom_reporting_status
STATUS_MAP = {
	"cleared": "reported",
	"submitted": "pending",
	"queued": "pending",
	"rejected": "rejected",
	"draft": "draft",
}
# Peppol MLS ResponseCode -> custom_reporting_status
MLS_MAP = {"AP": "reported", "AB": "pending", "RE": "rejected"}

AP_EVENTS = ("ap.invoice.received", "ap.credit_note.received")


def _json_or_text(r):
	try:
		return r.json()
	except Exception:
		return r.text


def _secret_of(settings):
	try:
		value = settings.get_password("webhook_secret", raise_exception=False)
	except Exception:
		value = None
	return value or settings.get("webhook_secret")


def _first_result(body):
	if isinstance(body, dict) and isinstance(body.get("results"), list) and body["results"]:
		return body["results"][0]
	return body if isinstance(body, dict) else {}


def _incoming_key(invoice):
	"""Same key for push and pull, so a pushed invoice is never imported twice."""
	seller = (invoice.get("AccountingSupplierParty") or {}).get("AccountingSupplierParty.TaxID") or {}
	return f"{seller.get('SellerTaxidentifier') or ''}:{invoice.get('InvoiceID') or invoice.get('ReferenceNumber') or ''}"


class KgrnAdapter(BaseAdapter):
	# PDF only exists after clearance, which normally comes back in the submit answer.
	AUTO_FETCH_DOCUMENTS_ON_SUBMIT = True
	DOCUMENT_FETCH_RETRY_ATTEMPTS = 2
	DOCUMENT_FETCH_RETRY_DELAY_SECONDS = 3
	PUSH_STATE = "submit"  # "draft" stores and validates without transmitting

	# ---------------------------------------------------------- auth
	def get_base_url(self):
		url = self.settings.base_url_override or self.settings.base_url
		if not url:
			url = LIVE_URL if self.settings.get("custom_environment") == "Production" else UAT_URL
		return url.rstrip("/")

	def _fetch_token(self):
		client_id = (self.settings.client_id or "").strip()
		secret = (self.settings.get_password("client_secret", raise_exception=False) or "").strip()
		if not client_id or not secret:
			frappe.throw(_("Enter the KGRN Client ID and Client Secret on E-Invoice Provider Settings."))
		r = requests.post(
			self.get_base_url() + TOKEN_PATH,
			json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret},
			timeout=TIMEOUT,
		)
		if not r.ok:
			frappe.throw(_("KGRN token request failed ({0}): {1}").format(r.status_code, r.text[:300]))
		body = r.json() or {}
		token = body.get("access_token") or body.get("token")
		if not token:
			frappe.throw(_("KGRN token response has no access_token."))
		ttl = min(int(body.get("expires_in") or 3600) - 300, OAUTH_TTL_SEC)
		self.set_cached_token(token, max(ttl, 60))
		return token

	def _token(self):
		if self.settings.auth_type == "oauth2":
			return self.get_valid_token()
		token = (self.settings.get_password("api_key", raise_exception=False) or "").strip()
		if not token:
			frappe.throw(_("Enter the KGRN API token (kgrn_out_...) in API Key on E-Invoice Provider Settings."))
		return token

	def get_auth_headers(self, extra=None):
		headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
		headers.update(extra or {})
		return headers

	def _request(self, method, path, **kwargs):
		url = self.get_base_url() + path
		extra = kwargs.pop("headers", None)
		r = requests.request(method, url, headers=self.get_auth_headers(extra), timeout=TIMEOUT, **kwargs)
		if r.status_code == 401 and self.settings.auth_type == "oauth2":
			frappe.cache().delete_value(self._cache_key())  # token died early: once more
			r = requests.request(method, url, headers=self.get_auth_headers(extra), timeout=TIMEOUT, **kwargs)
		return r

	def verify_auth(self):
		ok, message = False, ""
		try:
			r = self._request("GET", WHOAMI_PATH)
			body = _json_or_text(r)
			ok = r.ok
			message = json.dumps(body, indent=2) if isinstance(body, dict) else str(body)[:500]
			self.settings.db_set("last_test_response", message)
		except Exception as e:
			frappe.log_error(frappe.get_traceback(), "KGRN Verify Error")
			message = str(e)
		self.settings.db_set("last_test_status", "Passed" if ok else "Failed")
		self.settings.db_set("last_test_on", now_datetime())
		return {"status": "success" if ok else "error", "message": message}

	def get_participant_details(self):
		r = self._request("GET", WHOAMI_PATH)
		return {"http_status": r.status_code, "response": _json_or_text(r)}

	# ---------------------------------------------------------- send
	def build_payload(self, doc):
		return build_kgrn_payload(doc, self.settings, self.profile, self.PUSH_STATE)

	def submit_invoice(self, doctype, doc, json_data=None):
		payload = json_data or self.build_payload(doc)
		self.save_outgoing_payload(doc, payload)
		r = self._request("POST", OUTBOUND, json=payload, headers={"Content-Type": "application/json"})
		return r.status_code, _json_or_text(r)

	def parse_submit_response(self, status_code, response_data):
		"""201 = all ok, 207 = look at results[0]; validation errors keep it as a KGRN draft."""
		result = _first_result(response_data)
		raw = (result.get("status") or "").lower()
		ok = status_code in (200, 201, 207) and bool(result.get("success"))
		peppol = result.get("peppol") or {}

		if ok:
			reporting = STATUS_MAP.get(raw, raw or "pending")
		elif result.get("errorCode") == "PEPPOL_QUEUED":
			ok, reporting = True, "pending"  # KGRN retries by itself
		elif result.get("errorCode") == "PEPPOL_REJECTED":
			reporting = "rejected"
		else:
			reporting = "failed"

		return {
			"document_id": result.get("recordId") or result.get("uuid"),
			"reporting_status": reporting,
			"exchange_status": peppol.get("status") or result.get("errorCode") or raw or None,
			"success": ok,
		}

	# ---------------------------------------------------------- documents
	def _report(self, report, doc, accept="*/*"):
		params = {"referencenumber": doc.name, "financialyear": str(getdate(doc.posting_date).year)}
		return self._request("GET", REPORTS_PATH + report, params=params, headers={"Accept": accept})

	def get_document_xml(self, doctype, doc):
		r = self._report(REPORT_XML, doc, "application/xml")
		if not r.ok:
			frappe.throw(_("KGRN XML download failed ({0}): {1}").format(r.status_code, r.text[:300]))
		return r.text

	def get_document_pdf(self, doctype, doc):
		r = self._report(REPORT_PDF, doc, "application/pdf")
		if not r.ok or not r.content.startswith(b"%PDF"):
			frappe.throw(_("KGRN PDF not available yet ({0}): {1}").format(r.status_code, r.text[:300]))
		return r.content

	def get_document_status(self, doctype, doc):
		"""Status from the network's message level status (MLS) report."""
		r = self._report(REPORT_MLS, doc, "application/xml")
		code = None
		if r.ok and r.text.lstrip().startswith("<"):
			try:
				root = etree.fromstring(r.content, etree.XMLParser(resolve_entities=False, no_network=True))
				found = root.xpath("//*[local-name()='ResponseCode']/text()")
				code = found[0].strip() if found else None
			except Exception:
				code = None
		return {
			"http_status": r.status_code,
			"reporting_status": MLS_MAP.get(code) if code else None,
			"response": {"response_code": code, "mls": r.text[:5000]},
		}

	def get_status_from_document_status(self, body):
		if isinstance(body, dict):
			return MLS_MAP.get(body.get("response_code"))
		return None

	# ---------------------------------------------------------- AP push (KGRN configures it)
	def get_webhook_listener_url(self):
		return frappe.utils.get_url(
			"/api/method/einvoice_erpgulf.countries.ae.providers.kgrn.adapter.kgrn_ap_listener"
		)

	def register_webhook(self):
		"""KGRN has no subscribe API: their onboarding team sets the endpoint.
		This makes the secret and returns what to send them."""
		secret = _secret_of(self.settings) or frappe.generate_hash(length=40)
		self.settings.webhook_secret = secret
		self.settings.webhook_url = self.get_webhook_listener_url()
		self.settings.save(ignore_permissions=True)
		return {
			"message": "Send these to KGRN (AP inbound to ERP, API v2 payload).",
			"endpoint": self.settings.webhook_url,
			"authentication": "Bearer token = the Webhook Secret on this settings row",
		}

	def get_subscription(self):
		return {"endpoint": self.get_webhook_listener_url(), "configured_by": "KGRN onboarding"}

	def get_webhook_deliveries(self):
		return {"message": "Deliveries are in E-Invoice Webhook Log, and in KXample > Settings > AP inbound to ERP."}

	# ---------------------------------------------------------- AP pull
	def fetch_incoming(self, hours=24, limit=100):
		"""Every invoice received in the last `hours`, all pages."""
		out, offset = [], 0
		while True:
			r = self._request("GET", AP_PULL_PATH, params={"hours": hours, "limit": limit, "offset": offset})
			if not r.ok:
				frappe.throw(_("KGRN AP pull failed ({0}): {1}").format(r.status_code, r.text[:300]))
			body = _json_or_text(r)
			rows = body if isinstance(body, list) else (body.get("invoices") or body.get("data") or []) if isinstance(body, dict) else []
			out.extend(rows)
			total = int(r.headers.get("X-KGRN-Total") or 0)
			offset += len(rows)
			if not rows or len(rows) < limit or (total and offset >= total):
				return out

	# ---------------------------------------------------------- AP parsing
	def parse_incoming_invoice(self, invoice_json):
		"""v2 AP body (same shape as AR v2) -> the common incoming shape."""
		inv = invoice_json if isinstance(invoice_json, dict) else {}
		if "AccountingSupplierParty" not in inv or "InvoiceLine" not in inv:
			return {}

		seller = inv["AccountingSupplierParty"]
		lines = []
		for ln in inv.get("InvoiceLine") or []:
			qty_block = ln.get("InvoiceLine.InvoicedQuantity") or {}
			item = ln.get("InvoiceLine.LineItem") or {}
			tax = item.get("ClassifiedTaxCategory") or {}
			price = item.get("PriceDetails") or {}
			unit = qty_block.get("InvoiceLine.unitCode")
			name = item.get("InvoiceLine.Name")
			qty = flt(qty_block.get("InvoiceLine.Quantity")) or 1.0
			rate = flt(price.get("ItemNetPrice"))
			# real AP deliveries can carry a 0 line net: fall back to qty x net price
			amount = flt((ln.get("LineExtensionAmount") or {}).get("InvoiceLine.InvoiceLineNetAmount")) or qty * rate
			lines.append({
				"name": name,
				"description": item.get("InvoiceLine.Description") or name,
				"qty": qty,
				"uom": unit if unit and frappe.db.exists("UOM", unit) else "Nos",
				"rate": rate or (amount / qty),
				"amount": amount,
				"vat_rate": flt(tax.get("ClassifiedTaxCategory.Percent")) if tax.get("ClassifiedTaxCategory.Percent") not in (None, "") else 5.0,
			})

		return {
			"supplier_name": (seller.get("AccountingSupplierParty.PartyLegalEntity") or {}).get(
				"AccountingSupplierParty.RegistrationName"
			) or (seller.get("AccountingSupplierParty.PartyName") or {}).get("AccountingSupplierParty.Name"),
			"vat_number": (seller.get("AccountingSupplierParty.TaxID") or {}).get("SellerTaxidentifier"),
			"posting_date": inv.get("IssueDate"),
			"due_date": inv.get("DueDate") or (inv.get("PaymentTerms") or {}).get("TermsInstallmentDueDate"),
			"currency": inv.get("DocumentCurrencyCode") or "AED",
			"document_id": _incoming_key(inv),
			"conversion_rate": flt(inv.get("ExchangeRate")) or None,
			"payment_means_code": (inv.get("PaymentMeans") or {}).get("PaymentMeansCode"),
			"lines": lines,
		}


# ============================================================== incoming storage
def _store_incoming(invoice, meta=None):
	"""One E-Invoice Incoming Invoice per supplier TRN + invoice number. Returns its name or None if known."""
	key = _incoming_key(invoice)
	if frappe.db.exists("E-Invoice Incoming Invoice", {"document_id": key}):
		return None
	incoming = frappe.get_doc({
		"doctype": "E-Invoice Incoming Invoice",
		"document_id": key,
		"status": "Not Submitted",
		"submit_response": json.dumps(meta or {})[:1000],
	}).insert(ignore_permissions=True)
	file_doc = save_file(
		fname=f"kgrn-incoming-{frappe.scrub(key)}.json",
		content=json.dumps(invoice, indent=2, ensure_ascii=False),
		dt="E-Invoice Incoming Invoice",
		dn=incoming.name,
		is_private=1,
	)
	incoming.db_set("incoming_invoice_file", file_doc.file_url)
	return incoming.name


def _kgrn_rows():
	return frappe.get_all("E-Invoice Provider Settings", filters={"provider": PROVIDER, "enabled": 1}, pluck="name")


# ============================================================== AP push listener
def _bearer_matches(header):
	token = (header or "").removeprefix("Bearer ").strip()
	if not token:
		return False
	for name in _kgrn_rows():
		secret = _secret_of(frappe.get_doc("E-Invoice Provider Settings", name))
		if secret and hmac.compare_digest(secret, token):
			return True
	return False


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@rate_limit(limit=300, seconds=60)
def kgrn_ap_listener():
	"""KGRN posts each received supplier invoice here (AP v2: body = invoice,
	metadata in X-KGRN-* headers). Answer 2xx or KGRN records a failure."""
	raw = frappe.request.get_data(as_text=True) or ""
	try:
		body = json.loads(raw) if raw else {}
	except Exception:
		body = {}

	header = frappe.get_request_header
	event = header("X-KGRN-Event") or ""
	meta = {
		"event": event,
		"record_id": header("X-KGRN-Record-Id"),
		"instance_id": header("X-KGRN-Instance-Id"),
		"received_at": header("X-KGRN-Received-At"),
		"doc_type": header("X-KGRN-Doc-Type"),
		"entity_trn": header("X-KGRN-Entity-Trn"),
	}
	authorised = _bearer_matches(header("Authorization"))

	try:
		frappe.get_doc({
			"doctype": "E-Invoice Webhook Log",
			"webhook_response": raw,
			"document_id": meta["record_id"],
			"participant_id": header("X-KGRN-Receiver-Id"),
			"invoice_number": body.get("InvoiceID") if isinstance(body, dict) else None,
			"event_type": event,
			"status": "accepted" if authorised else "rejected: bad token",
		}).insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	except Exception:
		frappe.log_error(frappe.get_traceback(), "KGRN Webhook Log Error")

	if not authorised:
		frappe.local.response.http_status_code = 401
		return {"acknowledged": False}

	if event in AP_EVENTS and isinstance(body, dict) and body.get("InvoiceLine") is not None:
		try:
			_store_incoming(body, meta)
			frappe.db.commit()  # nosemgrep: frappe-manual-commit
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), "KGRN AP Import Error")
			frappe.local.response.http_status_code = 500  # KGRN shows it and can resend
			return {"acknowledged": False}
	return {"acknowledged": True}


# ============================================================== AP pull
@frappe.whitelist(methods=["POST"])
def pull_incoming_invoices(provider_settings: str | None = None, hours: int = 24):
	"""Button / manual: pull received invoices for one KGRN row (or every row)."""
	frappe.has_permission("Purchase Invoice", "create", throw=True)
	rows = [provider_settings] if provider_settings else _kgrn_rows()
	created = []
	for name in rows:
		adapter = KgrnAdapter(frappe.get_doc("E-Invoice Provider Settings", name))
		for invoice in adapter.fetch_incoming(hours=int(hours)):
			new = _store_incoming(invoice, {"source": "pull", "settings": name})
			if new:
				created.append(new)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return {"created": created, "count": len(created)}


def pull_incoming_scheduled():
	"""Hourly (hooks.py): last 2 hours, so a missed run overlaps; duplicates are skipped."""
	for name in _kgrn_rows():
		try:
			adapter = KgrnAdapter(frappe.get_doc("E-Invoice Provider Settings", name))
			for invoice in adapter.fetch_incoming(hours=2):
				_store_incoming(invoice, {"source": "pull", "settings": name})
			frappe.db.commit()  # nosemgrep: frappe-manual-commit
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"KGRN AP Pull Error - {name}")