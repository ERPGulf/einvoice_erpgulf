"""A-Cube UAE (gov-ae) Accredited Service Provider.

Base URL: https://ae-sandbox.api.acubeapi.com (sandbox)
          https://ae.api.acubeapi.com         (production)
Docs:     https://docs.acubeapi.com/documentation/gov-ae
OpenAPI:  https://docs.acubeapi.com/openapi/gov-ae-api.json

A-Cube takes the PINT-AE UBL document itself (self.build_ubl), exchanges it
over Peppol and reports the tax data document (TDD) to the FTA.

Login is repeated from the Belgian adapter on purpose: a country folder
never imports from another country.
"""

import hmac
import json
import uuid

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

INVOICES = "/invoices"
CREDIT_NOTES = "/credit-notes"
SEND_SUFFIX = "/outgoing"  # POST raw PINT-AE UBL XML -> 201
LEGAL_ENTITIES_PATH = "/legal-entities"
EVENT_DESTINATIONS_PATH = "/event-destinations"
WEBHOOK_EVENTS = ("document.delivered", "document.received")
WEBHOOK_AUTH_HEADER = "X-Acube-Token"

# A-Cube document status -> our custom_reporting_status.
# TODO confirm on the first sandbox run: A-Cube reports the TDD to the FTA as
# part of the exchange, so "delivered" is treated as FTA reported here.
STATUS_MAP = {
	"delivered": "reported",
	"accepted": "reported",
	"sending_failed": "failed",
	"sending_error": "failed",
	"delivery_failed": "failed",
	"delivery_error": "failed",
	"rejected": "rejected",
}

NS = {
	"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
	"cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}

def _q(prefix, tag):
	return "{%s}%s" % (NS[prefix], tag)


def _el(prefix, tag, text=None, **attrs):
	e = etree.Element(_q(prefix, tag), {k: str(v) for k, v in attrs.items() if v is not None})
	if text is not None:
		e.text = str(text)
	return e


def _insert_after(parent, ref, new):
	parent.insert(parent.index(ref) + 1, new)


def _status_text(value):
	"""A-Cube's status is a word on documents, but an HTTP code (int) on errors."""
	return value.lower() if isinstance(value, str) else ""

def _secret_of(settings):
	"""webhook_secret whether the field is a Password or a Data field."""
	try:
		value = settings.get_password("webhook_secret", raise_exception=False)
	except Exception:
		value = None
	return value or settings.get("webhook_secret")


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

	# ---------------------------------------------------------- legal entity
	def _our_tin(self):
		"""Participant ID '0235:1234567890' (or a TRN) -> '1234567890'."""
		raw = (self.settings.participant_id or self.settings.get("trn") or "").split("::")[-1]
		return "".join(c for c in raw.split(":")[-1] if c.isdigit())[:10]

	def _our_legal_entity(self):
		tin = self._our_tin()
		r = self._request("GET", LEGAL_ENTITIES_PATH)
		for e in _items(_json_or_text(r)):
			if (e.get("tin") or "") == tin:
				return e
		return None

	def verify_auth(self):
		"""Log in, then check our TIN is an Active legal entity on the account."""
		ok, message = False, ""
		try:
			self._fetch_token()
			tin = self._our_tin()
			entity = self._our_legal_entity() if tin else None
			if not tin:
				message = "Login OK, but Participant ID (0235:<10-digit TIN>) is empty."
			elif not entity:
				message = f"Login OK, but TIN {tin} is not a legal entity on this A-Cube account."
			elif (entity.get("status") or "").lower() != "active":
				message = f"Login OK, but legal entity {tin} is {entity.get('status')}, not Active. Finish onboarding."
			else:
				ok = True
				message = f"Login OK, legal entity {tin} is Active."
		except Exception as e:
			frappe.log_error(frappe.get_traceback(), "A-Cube UAE Verify Error")
			message = str(e)

		self.settings.db_set("last_test_status", "Passed" if ok else "Failed")
		self.settings.db_set("last_test_on", now_datetime())
		return {"status": "success" if ok else "error", "message": message}

	def get_participant_details(self):
		return self._our_legal_entity() or {"message": f"No legal entity for TIN {self._our_tin()}"}

	# ---------------------------------------------------------- send
	@staticmethod
	def _type_path(doc_or_type):
		if isinstance(doc_or_type, str):
			return CREDIT_NOTES if "credit" in doc_or_type.lower() else INVOICES
		return CREDIT_NOTES if doc_or_type.get("is_return") else INVOICES

	def submit_invoice(self, doctype, doc, json_data=None):
		xml = self.build_pint_ae(doc)

		# save_outgoing_payload writes JSON, so wrap the XML in a dict
		self.save_outgoing_payload(doc, {"content_type": "application/xml", "ubl_xml": xml.decode("utf-8")})

		path = self._type_path(doc) + SEND_SUFFIX
		r = self._request("POST", path, data=xml, headers={"Content-Type": "application/xml"})
		return r.status_code, _json_or_text(r)

	def parse_submit_response(self, status_code, response_data):
		"""201 with invoiceId / creditNoteId and status."""
		body = response_data if isinstance(response_data, dict) else {}
		document_id = body.get("invoiceId") or body.get("creditNoteId") or body.get("uuid") or body.get("id")
		success = status_code in (200, 201, 202) and bool(document_id)
		raw = _status_text(body.get("status"))
		return {
			"document_id": document_id,
			"reporting_status": STATUS_MAP.get(raw, raw or "sent") if success else "failed",
			"exchange_status": raw or None,
			"success": success,
		}

		# ---------------------------------------------------------- PINT-AE
	def _legal_registration(self, party_doc, fallback):
		"""(identifier, scheme agency code, authority) for PartyLegalEntity/CompanyID.
		Trade licence fields when filled, else the TIN (fallback)."""
		value = (
			party_doc.get("custom_trade_license_number")
			or party_doc.get("custom_legal_registration_identifier")
			or fallback
		)
		label = party_doc.get("custom_legal_registration_identifier_type") or "Commercial/Trade license"
		code = (getattr(self.profile, "legal_registration_types", {}) or {}).get(label, "TL")
		return value, code, party_doc.get("custom_legal_registration_authority")
	def _apply_payment_means(self, root, doc):
		"""PaymentMeansCode from the first payment row's Mode of Payment
		(custom_payment_means_codes, e.g. "10 - Cash"). For bank transfers the
		company IBAN goes in PayeeFinancialAccount (ibr-192-ae)."""
		code = None
		for row in doc.get("payments") or []:
			if row.mode_of_payment:
				value = frappe.db.get_value("Mode of Payment", row.mode_of_payment, "custom_payment_means_codes")
				if value:
					code = value.split(" - ")[0].strip()
					break
		pm = root.find("cac:PaymentMeans", NS)
		if pm is None:
			return
		code_el = pm.find("cbc:PaymentMeansCode", NS)
		if code:
			code_el.text = code
		code = code_el.text

		payee = pm.find("cac:PayeeFinancialAccount", NS)
		if code in ("30", "31", "42", "58"):
			if payee is None:
				bank = frappe.db.get_value(
					"Bank Account",
					{"company": doc.company, "is_company_account": 1, "disabled": 0},
					["iban", "bank_account_no", "account_name"],
					order_by="is_default desc",
					as_dict=True,
				)
				account_id = bank and (bank.iban or bank.bank_account_no)
				if account_id:
					payee = _el("cac", "PayeeFinancialAccount")
					payee.append(_el("cbc", "ID", account_id.replace(" ", "")))
					if bank.account_name:
						payee.append(_el("cbc", "Name", bank.account_name))
					pm.append(payee)
		elif payee is not None:
			pm.remove(payee)  # cash, card...: no bank account
			
	def build_pint_ae(self, doc):
		"""The shared UBL builder's document plus the PINT-AE pieces it doesn't
		add yet: BTAE-02 transaction type, BTAE-07 UUID, seller/buyer legal
		registration (IBT-030/047), item description, price base quantity and
		gross price, and the line amounts in AED (BTAE-08 / BTAE-10)."""
		xml = self.build_ubl(doc)
		if isinstance(xml, str):
			xml = xml.encode("utf-8")
		root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
		m = self.profile.money

		# header: ProfileExecutionID right after ProfileID, UUID right after ID
		if root.find("cbc:ProfileExecutionID", NS) is None:
			_insert_after(root, root.find("cbc:ProfileID", NS),
						  _el("cbc", "ProfileExecutionID", self.profile.transaction_type_flags(doc)))
		if root.find("cbc:UUID", NS) is None:
			stable = uuid.uuid5(uuid.NAMESPACE_URL, f"{frappe.local.site}/{doc.doctype}/{doc.name}")
			_insert_after(root, root.find("cbc:ID", NS), _el("cbc", "UUID", str(stable)))

		# legal registration on both parties (IBR-150 / IBR-136)
		company = frappe.get_doc("Company", doc.company)
		if doc.doctype == "Purchase Invoice":
			counterpart = frappe.get_doc("Supplier", doc.supplier)
			seller_doc, buyer_doc = counterpart, company
		else:
			counterpart = frappe.get_doc("Customer", doc.customer)
			seller_doc, buyer_doc = company, counterpart
		for path, party_doc in (
			("cac:AccountingSupplierParty/cac:Party", seller_doc),
			("cac:AccountingCustomerParty/cac:Party", buyer_doc),
		):
			party = root.find(path, NS)
			if party is None:
				continue
			endpoint = party.find("cbc:EndpointID", NS)
			value, code, authority = self._legal_registration(
				party_doc, endpoint.text if endpoint is not None else None
			)
			ple = party.find("cac:PartyLegalEntity", NS)
			if value and ple is not None and ple.find("cbc:CompanyID", NS) is None:
				_insert_after(ple, ple.find("cbc:RegistrationName", NS),
							  _el("cbc", "CompanyID", value, schemeAgencyID=code, schemeAgencyName=authority))

				# payment means from the invoice's Mode of Payment, as the other UAE adapters do
		self._apply_payment_means(root, doc)
		# lines
		for ln in root.findall("cac:InvoiceLine", NS) + root.findall("cac:CreditNoteLine", NS):
			qty_el = ln.find("cbc:InvoicedQuantity", NS)
			if qty_el is None:
				qty_el = ln.find("cbc:CreditedQuantity", NS)
			item = ln.find("cac:Item", NS)
			price = ln.find("cac:Price", NS)
			price_amount = price.find("cbc:PriceAmount", NS)
			currency = price_amount.get("currencyID")

			if item.find("cbc:Description", NS) is None:  # IBR-125
				item.insert(0, _el("cbc", "Description", item.find("cbc:Name", NS).text))

			if price.find("cbc:BaseQuantity", NS) is None:  # IBR-126
				_insert_after(price, price_amount, _el("cbc", "BaseQuantity", "1", unitCode=qty_el.get("unitCode")))
			if price.find("cac:AllowanceCharge", NS) is None:
				ac = _el("cac", "AllowanceCharge")
				ac.append(_el("cbc", "ChargeIndicator", "false"))
				ac.append(_el("cbc", "Amount", m(0), currencyID=currency))
				ac.append(_el("cbc", "BaseAmount", price_amount.text, currencyID=currency))
				price.append(ac)

			# IBR-147: price must reproduce the line net amount
			net = float(ln.find("cbc:LineExtensionAmount", NS).text)
			qty = float(qty_el.text or 0)
			if qty and abs(qty * float(price_amount.text) - net) > 0.005:
				price_amount.text = f"{net / qty:.6f}".rstrip("0").rstrip(".")
				ac = price.find("cac:AllowanceCharge", NS)
				ac.find("cbc:BaseAmount", NS).text = price_amount.text

			if ln.find("cac:ItemPriceExtension", NS) is None:  # BTAE-08 / BTAE-10
				pct_el = item.find("cac:ClassifiedTaxCategory/cbc:Percent", NS)
				tax = net * float(pct_el.text if pct_el is not None and pct_el.text else 0) / 100.0
				ext = _el("cac", "ItemPriceExtension")
				ext.append(_el("cbc", "Amount", m(net + tax), currencyID=currency))
				tt = _el("cac", "TaxTotal")
				tt.append(_el("cbc", "TaxAmount", m(tax), currencyID=currency))
				ext.append(tt)
				_insert_after(ln, price, ext)

		return etree.tostring(root, xml_declaration=True, encoding="UTF-8")

	# ---------------------------------------------------------- status
	def _document_id(self, doc):
		document_id = doc.get("custom_document_id")
		if not document_id:
			frappe.throw(_("Document ID not found on {0}. Send it to A-Cube first.").format(doc.name))
		return document_id

	def get_document_status(self, doctype, doc):
		r = self._request("GET", f"{self._type_path(doc)}/{self._document_id(doc)}")
		return {"http_status": r.status_code, "response": _json_or_text(r)}

	def get_status_from_document_status(self, body):
		if not isinstance(body, dict):
			return None
		raw = _status_text(body.get("status")) or _status_text(body.get("marking"))
		return STATUS_MAP.get(raw, raw or None)

	def get_document_xml(self, doctype, doc):
		"""The tax data document (TDD) A-Cube reported; falls back to the source XML."""
		base = f"{self._type_path(doc)}/{self._document_id(doc)}"
		for suffix in ("/tdd", "/source"):
			r = self._request("GET", base + suffix, headers={"Accept": "application/xml"})
			if r.ok:
				return r.text
		frappe.throw(_("A-Cube XML download failed ({0}): {1}").format(r.status_code, r.text[:300]))

	# get_document_pdf: the UAE API has no PDF endpoint, so the base class's
	# "not supported" message is kept.

	# ---------------------------------------------------------- webhooks
	def get_webhook_listener_url(self):
		return frappe.utils.get_url(
			"/api/method/einvoice_erpgulf.countries.ae.providers.acube.adapter.acube_webhook_listener"
		)

	def register_webhook(self):
		"""One event destination per event, each carrying our shared secret
		in a header that the listener checks."""
		secret = _secret_of(self.settings) or frappe.generate_hash(length=32)
		target = self.get_webhook_listener_url()
		results = {}
		for event in WEBHOOK_EVENTS:
			r = self._request("POST", EVENT_DESTINATIONS_PATH, json={
				"event": event,
				"target": target,
				"delivery": "webhook",
				"authType": "header",
				"authKey": WEBHOOK_AUTH_HEADER,
				"authToken": secret,
			})
			results[event] = {"http_status": r.status_code, "response": _json_or_text(r)}

		self.settings.webhook_secret = secret
		first = results[WEBHOOK_EVENTS[0]]["response"]
		if isinstance(first, dict):
			self.settings.webhook_uuid = first.get("eventId") or first.get("uuid") or first.get("id")
		self.settings.save(ignore_permissions=True)
		return results

	def get_subscription(self):
		r = self._request("GET", EVENT_DESTINATIONS_PATH)
		return {"http_status": r.status_code, "response": _json_or_text(r)}

	def get_webhook_deliveries(self):
		"""No delivery-log endpoint; the listener writes E-Invoice Webhook Log."""
		return {"message": "A-Cube deliveries are logged by acube_webhook_listener."}

	def delete_webhook(self, uuid=None):
		"""Without a uuid: delete every event destination that points at us."""
		if uuid:
			uuids = [uuid]
		else:
			ours = self.get_webhook_listener_url()
			listed = _items(_json_or_text(self._request("GET", EVENT_DESTINATIONS_PATH)))
			uuids = [d.get("eventId") or d.get("uuid") for d in listed if (d.get("target") or "").startswith(ours)]
		results = {u: self._request("DELETE", f"{EVENT_DESTINATIONS_PATH}/{u}").status_code for u in uuids if u}
		self.settings.db_set("webhook_uuid", None)
		return results

	# ---------------------------------------------------------- incoming
	def fetch_incoming_xml(self, document_id, document_type=None):
		"""Source XML of a received document; tries invoices, then credit notes."""
		paths = [self._type_path(document_type)] if document_type else [INVOICES, CREDIT_NOTES]
		for base in paths:
			r = self._request("GET", f"{base}/{document_id}/source", headers={"Accept": "application/xml"})
			if r.ok:
				return r.text
		frappe.throw(_("A-Cube incoming document {0} could not be fetched.").format(document_id))

	def parse_incoming_invoice(self, invoice_json):
		"""The file we store is {"acube_uuid", "document_type", "ubl_xml"}."""
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
				"vat_rate": float(vat) if vat else 5.0,
			})

		return {
			"supplier_name": supplier_name,
			"vat_number": vat_number,
			"posting_date": text(root, "cbc:IssueDate"),
			"due_date": text(root, "cbc:DueDate") or text(root, "cac:PaymentMeans/cbc:PaymentDueDate"),
			"currency": text(root, "cbc:DocumentCurrencyCode") or "AED",
			"document_id": invoice_json.get("acube_uuid") or text(root, "cbc:ID"),
			"conversion_rate": None,
			"payment_means_code": text(root, "cac:PaymentMeans/cbc:PaymentMeansCode"),
			"lines": lines,
		}


# ============================================================== webhook
def _ae_settings_rows():
	rows = frappe.get_all(
		"E-Invoice Provider Settings",
		filters={"provider": PROVIDER, "enabled": 1},
		fields=["name", "base_url", "base_url_override"],
	)
	return [r.name for r in rows if "//ae" in (r.base_url_override or r.base_url or "")]


def _token_matches(token):
	"""Constant-time compare against every UAE A-Cube row's webhook secret."""
	if not token:
		return False
	for name in _ae_settings_rows():
		secret = _secret_of(frappe.get_doc("E-Invoice Provider Settings", name))
		if secret and hmac.compare_digest(secret, token):
			return True
	return False


def _document_id_of(data):
	for key in ("documentId", "invoiceId", "creditNoteId", "document_id", "uuid", "id"):
		if data.get(key):
			return data[key]
	doc = data.get("document") or data.get("data") or {}
	if isinstance(doc, dict):
		for key in ("invoiceId", "creditNoteId", "uuid", "id"):
			if doc.get(key):
				return doc[key]
	return None


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
def acube_webhook_listener():
	"""A-Cube calls this for document.delivered and document.received.
	The shared secret arrives in the X-Acube-Token header; anything without
	it is logged and refused. Work is queued so A-Cube gets 200 fast."""
	raw = frappe.request.get_data(as_text=True) or ""
	try:
		data = json.loads(raw) if raw else {}
	except Exception:
		data = {}

	authorised = _token_matches(frappe.get_request_header(WEBHOOK_AUTH_HEADER))
	event = data.get("eventType") or data.get("event") or ""
	document_id = _document_id_of(data)

	try:
		frappe.get_doc({
			"doctype": "E-Invoice Webhook Log",
			"webhook_response": raw,
			"document_id": document_id,
			"event_type": event,
			"status": "accepted" if authorised else "rejected: bad token",
		}).insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	except Exception:
		frappe.log_error(frappe.get_traceback(), "A-Cube UAE Webhook Log Error")

	if not authorised:
		frappe.local.response.http_status_code = 401
		return {"acknowledged": False}

	if document_id and event in WEBHOOK_EVENTS:
		frappe.enqueue(
			"einvoice_erpgulf.countries.ae.providers.acube.adapter.process_webhook",
			queue="short",
			event=event,
			document_id=document_id,
			data=data,
		)
	return {"acknowledged": True}


def process_webhook(event, document_id, data):
	try:
		if event == "document.delivered":
			_refresh_outgoing(document_id)
		elif event == "document.received":
			_import_incoming_document(document_id, data)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), f"A-Cube UAE Webhook Processing Error - {event}")


def _refresh_outgoing(document_id):
	"""Re-read the status from A-Cube rather than trusting the webhook body."""
	from einvoice_erpgulf.core.status_sync import refresh_invoice_status

	for doctype in ("Sales Invoice", "Purchase Invoice"):
		name = frappe.db.get_value(doctype, {"custom_document_id": document_id}, "name")
		if name:
			refresh_invoice_status(doctype, name)


def _import_incoming_document(document_id, data):
	if frappe.db.exists("E-Invoice Incoming Invoice", {"document_id": document_id}):
		return

	document_type = data.get("documentType") or data.get("document_type")
	xml = None
	for row in _ae_settings_rows():
		try:
			xml = ACubeAdapter(frappe.get_doc("E-Invoice Provider Settings", row)).fetch_incoming_xml(
				document_id, document_type
			)
			break
		except Exception:
			continue  # not this account's document
	if not xml:
		frappe.throw(_("A-Cube incoming document {0} could not be fetched with any UAE A-Cube row.").format(document_id))

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