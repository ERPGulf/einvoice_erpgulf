# Developer Guide: countries and providers

**Intended for:** ERPGulf developers and partner developers working on the `einvoice_erpgulf` app — adding a new ASP adapter, maintaining an existing one, or debugging webhook and status-sync issues. The same adapter pattern applies to any country with a provider-based e-invoicing model, such as Oman or the European countries using Peppol access points, so partners building for those markets can use this guide as the reference design. It assumes you are comfortable with Frappe/ERPNext development in Python, but it is written so that a developer new to e-invoicing can follow it; every point says where its effect can be seen on screen.

Source code: [github.com/ERPGulf/einvoice_erpgulf](https://github.com/ERPGulf/einvoice_erpgulf). This guide matches `einvoice_erpgulf` 4.0 (October 2026), the multi-country successor of `uae_erpgulf`. The UAE is used as the worked example throughout; section 12 covers adding a country. The [End User Guide](USER_GUIDE.md) describes what the accountant sees; the [Testing Guide](TESTING_GUIDE.md) has the UI test script, including section 9 for a newly added ASP. The current list of registered ASPs is `einvoice_providers` in `hooks.py`; the adapters named in this guide are used as examples only.

## 1. The three files you will live in

### `core/base.py` — the contract

`BaseAdapter` is the list of jobs every ASP adapter must be able to do: get a token, verify the credentials, submit an invoice, read the submit response, ask for status, fetch the XML/PDF, manage webhooks, parse incoming invoices. It also gives you things for free:

- a token cache (`get_valid_token()`, 55 minutes by default, or whatever `expires_in` the ASP reports);
- `save_outgoing_payload(doc, payload)`, which attaches the exact JSON you sent to the invoice;
- `get_base_url()`, which honours the *Base URL Override* field on Provider Settings;
- `_not_supported("feature")`, which the base methods call so an ASP that lacks a feature shows "*feature* is not supported by *provider*" instead of a traceback. Leave the base method alone for anything your ASP cannot do.

Four class flags tell the common code how your ASP behaves: `AUTO_FETCH_DOCUMENTS_ON_SUBMIT` (XML/PDF are ready right after submit), `ATTACH_DOCUMENTS_IN_SYNC` (the scheduler should attach files, or your `get_document_status` does it itself), `STATUS_SYNC_ENABLED` (include this ASP in the 5-minute poll), `FINAL_REPORTING_STATUSES` (which statuses mean "stop polling").

| Method | What it must do | Required? |
| --- | --- | --- |
| `_fetch_token()` | Call the ASP's auth API, return a token (the base class caches it) | Yes |
| `get_auth_headers(extra=None)` | Headers for every authenticated call | Yes |
| `verify_auth()` | Test the credentials (the Verify / Test Auth button) | Yes |
| `submit_invoice(doctype, doc, json_data=None)` | Build the ASP's JSON from the invoice and POST it | Yes |
| `parse_submit_response(status_code, data)` | Turn the ASP reply into document_id + status | Yes |
| `get_document_status(doctype, doc)` | Fetch the latest status for an invoice | Yes |
| `get_status_from_document_status(body)` | Map the ASP's status wording to our Reporting Status | Yes |
| `get_document_xml` / `get_document_pdf` | Download the XML / PDF | If the ASP offers it |
| `get_participant_details()` | Read our own company profile from the ASP | Optional |
| `lookup_peppol_id(peppol_id)` / `update_participant(company_doc)` | Look up a Peppol participant / update our registration at the ASP | Optional |
| `register_webhook` / `get_subscription` / `get_webhook_deliveries` | Webhook management via API | Only if the ASP has a webhook API |
| `parse_incoming_invoice(json)` | Convert the ASP's received-invoice format into one common dict | If importing purchases |
| `get_webhook_listener_url()` | The URL to give the ASP for webhooks | If using webhooks |

`self.settings` is the E-Invoice Provider Settings row, so credentials and URLs always come from there and are never hardcoded.

**On screen:** *E-Invoice Provider Settings* → the Verify button runs `verify_auth`; *Last Test Status / Last Test On* are what it writes. On a submitted Sales Invoice the attached `…_payload.json` is `save_outgoing_payload` at work. A "… is not supported by …" message is `_not_supported`.

### `countries/<cc>/profile.py` — the country's rulebook (the UAE as the example)

Every provider in a country speaks the same dialect. For the UAE: scheme 0235 endpoint IDs, TIN = first 10 digits of the TRN, emirate codes, VAT category codes, UN/ECE unit codes, type codes 380/381/389/396, money rounded to 2 decimals and quantities to 6. You never retype any of that. A profile is a class (`AeProfile(PintProfile)`, `OmProfile(PintProfile)`, `BeProfile(EN16931Profile)`) whose parent in `core/standards/` carries the standard's defaults; the country file holds only what is national. Adapters reach it as `self.profile`:

| On `self.profile` | What it gives |
| --- | --- |
| `country_code`, `country_name`, `currency`, `currency_decimals` | identity; `money()` rounds to the country's decimals (OMR = 3) |
| `endpoint(participant_id)`, `endpoint_scheme`, `endpoint_id_from_tax_id(tax_id)` | Peppol participant identifier handling (UAE: TIN from TRN under 0235; Belgium: enterprise number under 0208) |
| `process_billing`, `process_self_billing`, `invoice_type_codes` | customization / profile IDs and UNCL1001 codes |
| `tax_category_code(label)`, `tax_codes` | 'S - Standard rated' → 'S', validated against the country's allowed set |
| `region_code(name)`, `region_field`, `resolve_address_codes(address)` | subdivision from the Address (Emirate, Governorate, none for Belgium) and the ISO country; throws the standard messages |
| `uom_to_unece(uom)`, `uom_piece_code` | UN/ECE units with the country's piece code (EA / H87 / C62) |
| `validate_document(doc)` | country rules run before anything is sent (Belgium: buyer reference required) |
| `ui_labels()` | authority short name and provider wording for the ribbon and messages |

The UAE profile also keeps module-level names for the adapters written before the profile existed:

| Group | Names | Notes |
| --- | --- | --- |
| Numbers | `dec`, `money`, `qty`, `num`, `TWO`, `SIX` | `Decimal(str(x))` based; `money` is 2 dp half-up, `qty` 6 dp, `num` strips trailing zeros |
| Peppol identifiers | `DEFAULT_ENDPOINT_SCHEME` (0235), `endpoint(peppol_id)`, `tin_from_trn(trn)` | `endpoint` returns `(id, scheme)`; the TIN is the first 10 digits of the 15-digit TRN |
| Process IDs | `PROCESS_BILLING`, `PROCESS_SELF_BILLING`, `INVOICE_TYPE_*` | PINT-AE customization / profile IDs and UNCL1001 codes 380, 381, 389, 396 |
| Code tables | `EMIRATE_CODES` + `emirate_code()`, `VAT_CODES` + `vat_category_code()`, `LEGAL_REGISTRATION_TYPES`, `DISCREPANCY_CODES`, `UOM_TO_UNECE` + `uom_to_unece()` / `uom_to_unece_table(piece_code)` | `vat_category_code` accepts the full label, the plain text or the bare code and throws on anything else; `uom_to_unece_table("H87")` for ASPs that want H87 instead of EA for pieces |
| Label parsing | `split_code(label)`, `transaction_type_pattern(doc)`, `transaction_type_flags(doc)` | `'30 - Credit transfer'` to `('30', 'Credit transfer')`; `'1XXXXXX : ...'` to `'1XXXXXX'` or the 8-bit `'10000000'` |
| Address | `country_code(name)`, `resolve_address_codes(address)` | returns `(ISO country, subdivision)` and throws the standard messages when the country is unknown or a UAE address has no emirate |

```python
from einvoice_erpgulf.countries.ae.profile import (
    PROCESS_BILLING, PROCESS_SELF_BILLING, UOM_TO_UNECE,
    dec as _dec, money as _money, endpoint as _endpoint,
    resolve_address_codes, split_code as _split_code,
    vat_category_code as _vat_category_code,
)
```

When you need a helper that is not there, add it to the country's `profile.py` if a second provider in that country would need it; to `core/standards/<standard>/` if every country on that standard would; keep it in the adapter if only that provider's API cares. New adapters should use `self.profile.<method>` rather than the module-level imports, so the same class can be registered for a second country (Oman) without edits.

**On screen:** *Company* → TRN (feeds `tin_from_trn`); *Address* → Emirate / Country (feeds `resolve_address_codes`); the VAT category on the invoice and its items (feeds `vat_category_code`); *UOM* on the invoice line (feeds `uom_to_unece`). When those are wrong the ASP rejects the invoice, and the fix is in the master data, not in code.

### `countries/<cc>/providers/<asp>/adapter.py` — the only ASP-specific file

It subclasses `BaseAdapter`, fills in the contract using the ASP's own API, and builds the JSON shape the ASP wants from the values `profile.py` gives you. Everything else — the submit button, the scheduler, the ribbon, the logs, the incoming-invoice doctype — is common code that never changes.

**On screen:** nothing of its own. Its effect is the whole flow working: submit → "Sent to ASP" ribbon → "FTA Reported" ribbon → XML/PDF attached.

## 2. The idea in one sentence

The UAE model is Peppol-based: every FTA-accredited ASP is an access point with its own API, auth and JSON, but ERPNext needs the same jobs from all of them. So the app isolates each ASP behind one adapter, and switching ASP is a dropdown.

How a call travels:

1. `Company.country` picks the profile from `einvoice_countries` in `hooks.py`; the Company field `custom_einvoice_provider` picks the ASP.
2. `get_adapter(settings)` reads the class path from `einvoice_providers[<country code>]` in `hooks.py`.
3. It returns that adapter, bound to the E-Invoice Provider Settings row and to the country profile (`self.settings`, `self.profile`).
4. Common code calls the same method names on whichever adapter comes back.
5. Results come back by webhook or by the scheduler poll.

**On screen:** *Company* → **Country** decides the rulebook (profile); *Company* → **E-Invoice Provider** (labelled *Accredited Service Provider* on a UAE site) decides which adapter the common code loads.

### Folder structure

```
einvoice_erpgulf/einvoice_erpgulf/
├── hooks.py                      # einvoice_countries, einvoice_providers, doc_events, scheduler
├── core/                         # country-neutral, provider-neutral — never names either
│   ├── base.py                   # BaseAdapter: the contract every provider follows (+ build_ubl)
│   ├── registry.py               # get_profile(), get_adapter(), registered_providers(), sync_provider_options()
│   ├── setup.py                  # after_install / after_migrate: country fields + provider dropdowns
│   ├── numbers.py                # dec, money(decimals), qty, num
│   ├── codes.py                  # ISO countries, UN/ECE units, UNCL1001 type codes, split_code
│   ├── standards/
│   │   ├── __init__.py           # BaseProfile — the country-profile contract
│   │   ├── pint/profile.py       # PintProfile: Peppol PINT defaults (UAE, Oman, SG, MY, A-NZ, JP)
│   │   ├── en16931/profile.py    # EN16931Profile: Peppol BIS 3.0 defaults (BE, NL, DE, Nordics)
│   │   └── ubl/builder.py        # UBL 2.1 Invoice / CreditNote from an ERPNext invoice + a profile
│   ├── provider_settings.py      # the active Provider Settings row for a company
│   ├── submit_sales.py           # Sales Invoice on_submit hook, buttons, bulk submit
│   ├── send_purchase.py          # the same for Purchase Invoice
│   ├── status_sync.py            # 5-minute scheduler poll, every country, every provider
│   ├── attach.py                 # buttons for status / XML / PDF, attaching files
│   ├── webhook.py                # webhook buttons + logs
│   ├── incoming.py               # "Get Incoming Invoices" import
│   ├── verify_token.py           # Verify button
│   └── validation.py             # checks before submit / cancel, provider registered, profile.validate_document
│
├── countries/
│   ├── ae/
│   │   ├── profile.py            # AeProfile(PintProfile): 0235, TIN from TRN, emirates, VAT codes, PINT-AE IDs
│   │   ├── fixtures/custom_field.json   # UAE-only fields and labels (Emirate, FZ beneficiary, RCM codes…)
│   │   └── providers/
│   │       ├── flick/            # adapter.py, sales_json.py, purchase_json.py
│   │       ├── marmin/adapter.py
│   │       └── suntech/adapter.py
│   ├── om/   profile.py, fixtures/, providers/      # Oman (PINT-OM)
│   └── be/   profile.py, fixtures/, providers/      # Belgium (BIS 3.0)
│
├── e_invoice/                    # the Frappe module: doctypes, dashboard page, reports, workspace
├── fixtures/custom_field.json    # shared custom fields (generic names: custom_einvoice_*)
└── public/js/                    # form scripts: buttons, ribbon (einvoice_common.js), tooltips, bulk submit
```

The rule has three parts. If the code mentions a provider's URL, field name or JSON key, it goes inside that provider's folder. If two providers of the same country would have to agree on it for the tax authority to accept both invoices (a code table, a rounding rule, how a tax number becomes a Peppol ID), it goes in that country's `profile.py` — or in `core/standards/` when every country on that standard shares it. If it works the same for every country and is about ERPNext rather than the invoice format (buttons, scheduler, attachments, logs), it stays in `core/`.

## 3. A customer walks in with "ASP XYZ"

Ask the customer for four things from XYZ: the API documentation, sandbox credentials, the webhook mechanism (JWT, HMAC, UUID…) and a sample success and rejection response. Then check whether XYZ is already in the registry: if it is, there is no development at all — configure and test.

**On screen:** *E-Invoice Provider Settings* → Provider dropdown. If XYZ is selectable there and on Company without an error on save, the adapter exists. `registered_providers()` is whitelisted, so `/api/method/einvoice_erpgulf.core.registry.registered_providers` returns the list.

## 4. What changes between ASPs, and what never does

Before anyone opens an editor, set expectations: most of the app does not care which ASP it is. The adapters already in the tree are the evidence.

### What never changes (common code — don't touch it)

- **The invoice data itself.** TRN, emirate, VAT category, UOM, type code, rounding — all of it comes from `profile.py` and the same Company/Customer/Address/Item fields. Every existing adapter builds its payload from the same `profile.py` calls; only the JSON key names differ.
  *On screen:* the same master data serves every ASP; switch the Company dropdown and nothing in the masters needs editing.
- **The user experience.** Submit, the "E-Invoice Response" popup, the corner ribbon, Fetch Status, XML/PDF attachments, the cancel block, Webhook Logs, Incoming Invoices — identical for every ASP, because it is all common code reading `custom_reporting_status` and `custom_document_id`.
  *On screen:* an invoice sent through one ASP and one sent through another look the same in the list and on the form.
- **The scheduler.** `sync_pending_invoices` runs every five minutes for every registered provider and calls *your* `get_document_status`; you never write a scheduler.
- **Settings storage.** One *E-Invoice Provider Settings* row per ASP and company, with the same fields: client id, secret, base URL override, webhook secret, participant id, last test status. Your adapter reads them from `self.settings`; you don't add a doctype.

### What does change (this is the adapter)

- **Authentication.** Flick does OAuth `client_credentials` → bearer token. Marmin signs the token request with HMAC-SHA256 of the client id, keyed with the secret, in an `x-marmin-signature` header. Suntech posts to a token endpoint and reports `expires_in`, which the adapter passes to the cache. Future ASPs may use mTLS certificates, signed JWT assertions, or a static key per company. All of that lives inside `_fetch_token` / `get_auth_headers`; the token cache in `base.py` handles expiry either way.
  *On screen:* the Verify button on Provider Settings; which fields the user fills in differs slightly.
- **Payload shape.** Same PINT-AE values, different envelope and key names, sometimes a separate builder module (Flick keeps `sales_json.py` / `purchase_json.py`). A future ASP may want raw UBL XML, a multipart upload, or a batch array of invoices. Only `_build_payload` (or the builder module) knows this.
  *On screen:* the attached `…_payload.json` on the invoice. Compare one from each ASP and you see the whole difference.
- **Submit response and status wording.** "ACCEPTED", "SUBMITTED", "VALIDATED", "REPORTED", numeric codes — every ASP has its own vocabulary, and `parse_submit_response` / `get_status_from_document_status` translate it into the app's reporting statuses; `FINAL_REPORTING_STATUSES` says which ones are terminal. Some ASPs answer synchronously with the FTA result; most answer "received" and the real result arrives later.
  *On screen:* the ribbon straight after submit — "Sent to ASP" or already "FTA Reported" — tells you which kind of ASP it is.
- **How results come back.** Flick exposes a webhook API, so the Subscribe Webhook button calls `register_webhook()`. Marmin and Suntech are portal-only: the URL and secret are pasted in the ASP's portal and the adapter's `register_webhook` throws a message saying so. A future ASP may offer no webhook at all — then rely on the scheduler poll; or may push with HMAC signatures, JWT or a shared UUID — the verification method is adapter code.
  *On screen:* the Webhook URL shown on Provider Settings; rows in *E-Invoice Webhook Log* (or none, for a polling-only ASP).
- **Documents.** Some ASPs return the signed XML and PDF right after submit (`AUTO_FETCH_DOCUMENTS_ON_SUBMIT = True`); Suntech gives download paths in the status response, so it sets `AUTO_FETCH_DOCUMENTS_ON_SUBMIT = False` and `ATTACH_DOCUMENTS_IN_SYNC = False` and attaches the files inside its own status handling; an ASP may give XML only and no PDF (leave `get_document_pdf` to the base class → "PDF download is not supported"). Future ASPs may hand out a download link with an expiry instead of the file.
  *On screen:* when the XML/PDF attachments appear — immediately or a few minutes later — and whether the PDF button says "not supported".
- **Incoming (purchase) invoices.** Each ASP delivers received invoices in its own format (pull endpoint, webhook payload, or both). `parse_incoming_invoice` turns it into the one common dict that `get_purchase_inv.py` understands; an ASP may offer nothing here until later.
  *On screen:* the *E-Invoice Incoming Invoice* list fills, or stays empty.
- **Sandbox vs production.** Different base URLs, sometimes different credentials. That is the *Base URL Override* field, not code.
  *On screen:* Provider Settings → Base URL Override.

### What may come in the future that the design already allows

- ASPs with per-company (multi-tenant) credentials rather than one account — Provider Settings is already keyed by company.
- ASPs that require pre-registration of the customer's endpoint ID before the first invoice — do it inside `verify_auth` or `update_participant`; no common-code change.
- Rate limits and batch submission — the adapter can queue inside `submit_invoice`; the invoice still shows "Sent to ASP" until status arrives.
- Replacing an ASP for an existing customer — change the Company dropdown; old invoices keep their `custom_document_id` and ribbon, new ones go through the new adapter.

**The one-line rule:** if a change you are about to make is not inside `countries/<cc>/providers/<asp>/`, stop — either the thing is already common code, or it belongs in `profile.py` for everyone.

## 5. Register it in hooks.py before writing a line of adapter code

Add one line under the right country in `einvoice_providers` in `hooks.py`, create the folder `countries/<cc>/providers/<asp>/` with an empty `__init__.py` and an `adapter.py` containing just the class, then:

```bash
bench --site <site> clear-cache
bench --site <site> migrate
sudo supervisorctl restart frappe-bench-web-72:*   # or your bench's supervisor group
```

```python
einvoice_providers = {
    "AE": {
        ...
        "New ASP Name LLC": "einvoice_erpgulf.countries.ae.providers.newasp.adapter.NewAspAdapter",
    },
}
```

The key is the provider name exactly as it will appear in the dropdowns; the outer key is the country code, and the same adapter class may be listed under several countries. `migrate` runs `sync_provider_options` (hooked on `after_migrate` and `after_install`), which appends the new name to the Provider dropdown on E-Invoice Provider Settings and to *Accredited Service Providers* on Company; the existing FTA list is kept. Doing this first means every later test is a real end-to-end test. `hooks.py` is cached in Redis, so `clear-cache` and a worker restart are not optional.

**On screen:** after migrate, both dropdowns show the new name. Selecting a provider that has no adapter makes Company (and Provider Settings) refuse to save with "No adapter is registered for provider …" — that is `assert_provider_registered`.

## 6. Write the adapter — five parts

Copy the newest, most complete adapter under `countries/ae/providers/` as a starting point and change it part by part.

1. **Auth** — `_fetch_token()`, `get_auth_headers()`, `verify_auth()`: read `client_id` / `get_password("client_secret")` from `self.settings`, call the token URL, return the token; `verify_auth` should request a fresh token and set `last_test_status` / `last_test_on`. Always pass `timeout=`.
   *On screen:* Provider Settings → Verify → Last Test Status turns Success.
2. **Submit** — `_build_payload()` and `submit_invoice()`: map Sales/Purchase Invoice, Company, Customer/Supplier and Address fields into the ASP's JSON using `profile.py` helpers, call `self.save_outgoing_payload(doc, payload)`, POST, return `parse_submit_response()` with the document id and status.
   *On screen:* submit a Sales Invoice → "E-Invoice Response" popup, ribbon "Sent to ASP", `custom_document_id` filled, payload file attached.
3. **Status** — `get_document_status()` and `get_status_from_document_status()`: GET by document id, map the ASP's wording to the app's reporting statuses.
   *On screen:* the ribbon moving to "FTA Reported" / "FTA Pending" / "FTA Rejected" within five minutes (scheduler), or immediately via the status button.
4. **Documents** — `get_document_xml()` / `get_document_pdf()`: download; the common code attaches.
   *On screen:* XML and PDF attachments on the invoice.
5. **Webhook and incoming** — a whitelisted guest method that verifies the request with `webhook_secret`, logs the call, finds the invoice by `custom_document_id` and updates its status; `parse_incoming_invoice()` for received invoices.
   *On screen:* *E-Invoice Webhook Log* (one row per call), *E-Invoice Incoming Invoice*, and the Webhook URL on Provider Settings.

A minimal skeleton:

```python
import frappe
import requests
from einvoice_erpgulf.core.base import BaseAdapter

TIMEOUT = 60


class NewAspAdapter(BaseAdapter):
    AUTO_FETCH_DOCUMENTS_ON_SUBMIT = False

    def _fetch_token(self):
        r = requests.post(f"{self.get_base_url()}/auth/token", data={
            "client_id": self.settings.client_id,
            "client_secret": self.settings.get_password("client_secret"),
        }, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()["access_token"]

    def get_auth_headers(self, extra=None):
        headers = {"Authorization": f"Bearer {self.get_valid_token()}"}
        headers.update(extra or {})
        return headers

    def verify_auth(self):
        return {"ok": bool(self._fetch_token())}

    def submit_invoice(self, doctype, doc, json_data=None):
        payload = json_data or self._build_payload(doctype, doc)
        self.save_outgoing_payload(doc, payload)
        r = requests.post(f"{self.get_base_url()}/invoices", json=payload,
                          headers=self.get_auth_headers(), timeout=TIMEOUT)
        return self.parse_submit_response(r.status_code, r.json())

    def get_document_status(self, doctype, doc):
        ...

    def _build_payload(self, doctype, doc):
        # read everything from doc / company / customer fields, never hardcode;
        # codes and rounding come from self.profile, only the JSON shape is yours
        p = self.profile
        endpoint_id, scheme = p.endpoint(self.settings.participant_id)
        process = p.process_self_billing if doctype == "Purchase Invoice" else p.process_billing
        lines = [{
            "amount": p.money(item.net_amount),
            "vat_category": p.tax_category_code(doc.custom_vat_category),
        } for item in doc.items]
        ...
```

### Webhooks and status sync

Each ASP sends webhooks differently, so the listener lives inside that ASP's adapter file. Every listener ends the same way:

1. A whitelisted guest method receives the POST, e.g. `providers.<asp>.adapter.<asp>_webhook_listener`.
2. It verifies the request with the ASP's method (JWT, HMAC signature, UUID) using `webhook_secret` from Provider Settings. A listener that trusts the payload without verification will not be merged; if the ASP cannot sign, treat the payload as a pointer and re-fetch the status from the ASP before writing to any invoice.
3. It writes a row to E-Invoice Webhook Log.
4. It finds the invoice by `custom_document_id`, updates `custom_reporting_status` and attaches the XML/PDF when ready.
5. For a received invoice, it creates a E-Invoice Incoming Invoice record.

Webhooks can be missed, especially when the ASP doesn't retry. `status_sync.sync_pending_invoices` (scheduled every 5 minutes in `hooks.py`) already covers every ASP: it picks submitted invoices from the last 7 days that are not final yet or still miss their XML/PDF, and calls the company's adapter. A new ASP needs no scheduler entry of its own; it only needs a working `get_document_status`, plus the class flags from section 1 if its behaviour differs.

## 7. Custom fields — usually none

The app's custom fields (`custom_einvoice_enabled`, `custom_einvoice_provider`, `custom_document_id`, `custom_reporting_status`, `custom_einvoice_status`, TRN, emirate, VAT category…) live in `fixtures/custom_field.json` under module `einvoice_erpgulf` and already cover what PINT-AE needs. Only add a field if the ASP requires something no other ASP needs (like a profile id). Then give it *Depends On* for that provider, read it in your `_build_payload`, and export it to the same fixture.

**On screen:** *Customize Form* → Sales Invoice / Company / Address → fields whose names start with `custom_` and module `einvoice_erpgulf`.

## 8. Configure and switch on

Create a Provider Settings row for the ASP (credentials, Base URL Override for sandbox, webhook secret), select the ASP on Company, tick **E-Invoice Enabled**. With the box unticked nothing is sent — that is the early return at the top of `submit_sales.py` / `send_purchase.py`. Invoices that have been sent to the FTA cannot be cancelled (`validation.block_cancel_if_reported`); the user is told to create a Credit Note instead.

**On screen:** Company → E-Invoice Enabled; Provider Settings → Verify → Success.

## 9. Rules for a pull request

- Imports from `profile.py`; no copied code tables, no re-declared emirate / VAT / UOM / Peppol-ID logic.
- `timeout=` on every `requests` call. Without it a stalled ASP hangs a web or scheduler worker indefinitely.
- Webhook verified, or payload treated as a pointer and re-fetched.
- No `raise NotImplementedError`; leave unsupported base methods alone (`_not_supported`).
- No hardcoded values: amounts, codes, TRN and party details from invoice, Company, Customer or Supplier fields; credentials from Provider Settings.
- Purchase invoices flip the parties: on a purchase (self-billing) the supplier is the seller and our company is the buyer. Check every party block.
- Returns differ per ASP: a purchase return links to a Purchase Invoice, not a Sales Invoice; some ASPs use a separate endpoint or document type for debit and credit notes.
- Don't break tested flows: when fixing one document type, change only that branch of the adapter.
- No `_()` at module or class level (Semgrep: frappe-breaks-multitenancy). Wrap messages in a method.
- Never use the `Authorization: Bearer` header for webhooks. Frappe intercepts it and returns 401 before your guest method runs; ask the ASP to send the token in a custom header (e.g. `X-TCA-Token`).
- The webhook URL must be reachable from the internet, not just the office. Test from mobile data.
- TIN vs TRN: the Peppol endpoint (scheme 0235) uses the 10-digit TIN, the first 10 digits of the 15-digit TRN (`pint.tin_from_trn`).
- Strip credentials: a trailing space in `client_id` or secret gives a 401 with an empty body.
- Tabs, ruff-clean; the Testing Guide's section 9 run against the sandbox, with screenshots in the PR.

## 10. Prove it and ship

Run the user-visible checks: Verify auth; a normal invoice (380); a credit note (381); a debit note (383/396 as the ASP expects); a purchase invoice; a rejected invoice (bad TRN) and its ribbon; a cancel attempt on a reported invoice (must be blocked); a webhook received; the scheduler catching up with the webhook disabled; an incoming invoice imported. Then open the PR against `version-3`; it is merged to `main` after review.

**On screen:** ribbons on the invoice list, Webhook Logs, Incoming Invoices, and the cancel-blocked message.

## 11. Adding a country

A country is a profile class plus one line in `hooks.py`; providers come after, exactly as above.

1. **Pick the standard.** PINT (`core/standards/pint`) for the UAE-style markets — Oman, Singapore, Malaysia, Australia/NZ, Japan. EN 16931 / BIS 3.0 (`core/standards/en16931`) for Europe. If the country uses neither (CII / Factur-X, a clearance platform), that is a new folder under `core/standards/` first.
2. **Write `countries/<cc>/profile.py`.** Subclass the standard and set only what is national: `country_code`, `country_name`, `authority_short`, `provider_label`, `currency` and `currency_decimals`, `endpoint_scheme`, `endpoint_id_from_tax_id`, `region_field` / `region_codes` / `region_required`, `tax_codes` if narrower, `pint_suffix` (PINT) and `validate_document` for national rules. `countries/om/profile.py` is the shortest example; `countries/be/profile.py` shows a European one.
3. **Add country fields** in `countries/<cc>/fixtures/custom_field.json` — the region field on Address if the country has one, and any national registration fields. Re-declaring a shared field there (for example `Company-custom_einvoice_provider` with the label *Accredited Service Provider*) changes its label on sites in that country. These are installed by `bench migrate` for every country that has a Company.
4. **Register** it: `einvoice_countries["<Country name as on Company.country>"] = "einvoice_erpgulf.countries.<cc>.profile.<Cc>Profile"` and an empty `"<CC>": {}` under `einvoice_providers`.
5. **Providers.** Each is `countries/<cc>/providers/<name>/adapter.py`. If a UAE adapter's API is identical for the new country (the same ASP accredited in Oman), list the existing class under `"OM"` — no new code. For a provider that takes UBL XML rather than JSON (every European access point), `submit_invoice` is `xml = self.build_ubl(doc)` followed by the POST; `core/standards/ubl/builder.py` does the document from the profile.
6. **Test** with the Testing Guide section 9 on a Company whose Country is the new one; the ribbon labels, dropdown wording and address validation should all read as that country's.

**On screen:** *Company* → Country; after `bench migrate`, the country's fields appear on Address / Customer / Supplier and the Company provider field carries that country's label.

## 12. Read more

`README.md` for the overview and the migration from `uae_erpgulf`, `docs/TESTING_GUIDE.md` for the test rows, `docs/USER_GUIDE.md` for what the customer sees, `core/registry.py` for how countries and providers are resolved, and `core/standards/__init__.py` for the full profile contract.
