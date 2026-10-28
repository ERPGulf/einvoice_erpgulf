# E-Invoicing for ERPNext (einvoice_erpgulf)

A Frappe / ERPNext open source app for every country where e-invoices go out through a **provider the taxpayer chooses** — a Peppol access point, an accredited service provider, a certified platform. It submits Sales and Purchase Invoices through that provider, tracks their status with the tax authority, stores the returned XML and PDF against the invoice, receives webhooks, and imports incoming supplier invoices.

One app, one folder per country, one folder per provider under it. The common code never names a country or a provider: the Company's **Country** selects the country profile (the rulebook: identifier scheme, tax categories, region codes, decimals, customization IDs), the Company's **E-Invoice Provider** selects the adapter. Adding a country is one `profile.py`; adding a provider is one `adapter.py` plus one line in `hooks.py`.

| | |
| --- | --- |
| Countries shipped | **United Arab Emirates** (PINT-AE, FTA, accredited ASPs) — in production. **Oman** (PINT-OM, Oman Tax Authority) — profile ready, four constants to confirm against the OTA specification. **Belgium** (Peppol BIS 3.0 / EN 16931, mandatory B2B since 2026) — profile ready, adapters per access point. Any other PINT or EN 16931 country is a profile away; the [Country Guide](docs/COUNTRIES.md) covers each one. |
| Providers | Provider-agnostic. Each is one folder under `countries/<cc>/providers/`; the live list is `einvoice_providers` in `hooks.py`. Pull requests for new providers and countries are welcome. |
| Documents | Sales Invoice (invoice, credit note), Purchase Invoice (self-billed invoice and credit note), incoming supplier invoices |
| Frappe / ERPNext | Version 15 and 16 · also available on Frappe Cloud |
| Licence | MIT (see `license.txt`) — 100 % open source |
| Maintainer | [ERPGulf](https://www.erpgulf.com) · support@erpgulf.com |

Successor of `uae_erpgulf`. Existing sites move over with one command — see [Migrating from uae_erpgulf](#migrating-from-uae_erpgulf).

## Documentation

| Document | For whom | What it covers |
| --- | --- | --- |
| [End User Guide](docs/USER_GUIDE.md) | Accountants and admins | Setting up Company, Provider Settings and master data; submitting invoices; statuses and the ribbon; credit notes; purchase invoices; importing incoming invoices; dashboard, reports, logs; error messages |
| [Testing Guide](docs/TESTING_GUIDE.md) | Implementers and QA | Step-by-step UI test script (written on a UAE company) with expected results for every screen and button; acceptance checklists for a newly added provider (section 9) and a newly added country (section 10); result sheet |
| [Developer Guide](docs/DEVELOPER_GUIDE.md) | ERPGulf developers and partners | Architecture, the adapter contract, country profiles and standards, how to add a provider, how to add a country, webhooks and status sync, rules and gotchas |
| [Country Guide](docs/COUNTRIES.md) | Developers, partners, implementers | Which countries fit the app and how big each port is: full fact sheets for the UAE, Oman and Belgium; candidates on the same model (Singapore, Malaysia, Bahrain) and on Peppol with a profile (Australia/NZ, Japan, Netherlands, Nordics, Germany, France); the clearance-platform countries that stay separate apps; a ten-question checklist that yields a complete country profile |

## How it works

```
Sales / Purchase Invoice ──submit──▶ core/validation.py ──▶ core/submit_sales.py / core/send_purchase.py
                                                                        │
                        core/registry.py: Company.country ─▶ profile,  Company provider ─▶ adapter
                                                                        │
              ┌─────────────────────────────┬───────────────────────────┴────────────────────────┐
     countries/ae/providers/<asp>/   countries/om/providers/<asp>/                countries/be/providers/<ap>/
     countries/ae/profile.py         countries/om/profile.py                      countries/be/profile.py
              └─────────────────────────────┴────────────────────────────────────────────────────┘
                              core/standards/  pint/  en16931/  ubl/      (the rulebooks and the UBL builder)
                              core/base.py                                (BaseAdapter: the provider contract)
```

1. On Company, **E-Invoice Enabled** is ticked and a provider is chosen; the Company's **Country** decides which rulebook applies.
2. One **E-Invoice Provider Settings** row per company holds that provider's credentials, URLs and webhook secret.
3. When a Sales or Purchase Invoice is submitted, the app checks the setup and the country rules, builds the provider's payload (JSON for most UAE ASPs, UBL XML for European access points) from the invoice, Company, Customer/Supplier and Address fields, and sends it. The response, document ID and status are stored on the invoice.
4. Status moves to *reported* / *delivered* / *rejected* / *failed* either by **webhook** from the provider or by the **scheduler** (`core/status_sync.py`, every 5 minutes), which also attaches the XML and PDF once ready.
5. Incoming supplier invoices arrive into **E-Invoice Incoming Invoice** and can be turned into draft Purchase Invoices from the Purchase Invoice form.
6. An invoice that has been sent cannot be cancelled; the user is told to create a Credit Note instead.

## Adding a country or a provider

Both are code, registered in `hooks.py`; nothing is added through the UI. The UI picks them up on `bench migrate`. Where the user sees the result is noted for each step.

### Where the country is set

The app has no country field of its own. It reads the standard ERPNext field **Company → Country**: that choice selects the country profile (`einvoice_countries` in `hooks.py` → `countries/<cc>/profile.py`), and the profile decides which providers may be chosen, which fields appear, how identifiers and tax categories are coded, and what the ribbon and messages call the tax authority. One site can hold companies in different countries; each Company follows its own country.

### Adding a provider (ASP / access point) to an existing country

1. Write `countries/<cc>/providers/<name>/adapter.py` with a class subclassing `einvoice_erpgulf.core.base.BaseAdapter`. The [Developer Guide](docs/DEVELOPER_GUIDE.md) has the skeleton and the method list; copy the newest adapter under `countries/ae/providers/` as a starting point.
2. Add one line under that country in `einvoice_providers` in `hooks.py`. The key is the provider name exactly as it must appear in the dropdowns:

   ```python
   einvoice_providers = {
       "AE": {
           ...
           "New ASP Name LLC": "einvoice_erpgulf.countries.ae.providers.newasp.adapter.NewAspAdapter",
       },
   }
   ```

   The same adapter class may be listed under more than one country when a provider is accredited in several — list it again under `"OM"`, no new code.
3. `bench --site <site> clear-cache && bench --site <site> migrate`, then restart the workers (hooks are cached in Redis).

**On screen:** the name is now selectable on *Company → E-Invoice Provider* and on *E-Invoice Provider Settings → Provider* (`sync_provider_options` appends it on migrate; existing options are never removed). A name that is not registered for the company's country is refused on save with "No adapter is registered for provider …", which is the check that keeps a typo or an unbuilt provider from reaching an invoice.

### Adding a country

1. Pick the standard: `PintProfile` (`core/standards/pint`) for Peppol PINT markets — UAE, Oman, Singapore, Malaysia, Australia/NZ, Japan; `EN16931Profile` (`core/standards/en16931`) for Peppol BIS 3.0 in Europe. A country that uses neither (CII / Factur-X, a clearance platform) needs a new folder under `core/standards/` first.
2. Write `countries/<cc>/profile.py` subclassing it, setting only what is national: `country_code`, `country_name`, `authority_short`, `provider_label`, `currency` and `currency_decimals`, `endpoint_scheme`, `endpoint_id_from_tax_id`, `region_field` / `region_codes` / `region_required`, `pint_suffix` (PINT), `validate_document` for national rules. `countries/om/profile.py` is the shortest example, `countries/be/profile.py` the European one.
3. Add `countries/<cc>/fixtures/custom_field.json` with the country's own fields (a region field on Address, national registration fields) and any shared field whose label should differ in that country (for example `Company-custom_einvoice_provider` labelled *Accredited Service Provider*).
4. Register it in `hooks.py`: one line in `einvoice_countries` (key = the country name as on Company → Country) and an empty `"<CC>": {}` block in `einvoice_providers`.
5. Add providers as above.
6. Create a Company with that Country and run `bench --site <site> migrate`: the country fields are installed only for countries that have a Company, so a Belgian site never sees an Emirate field.

**On screen:** after migrate, the country's fields appear on Address / Customer / Supplier / invoices, the Company's provider field carries that country's label, the Provider dropdown offers that country's providers, and the ribbon names that country's authority ("FTA Reported", "OTA Reported", "FPS Finance Reported").

### What never needs a change

`core/` — submit, status sync, attachments, webhooks logging, incoming-invoice import, the cancel block, the ribbon, the three doctypes. If a change you are about to make is outside `countries/<cc>/`, it is either already common code or belongs in `core/standards/` for every country on that standard.

## Installation

```bash
cd frappe-bench
bench get-app https://github.com/ERPGulf/einvoice_erpgulf.git
bench --site <site> install-app einvoice_erpgulf
bench build --app einvoice_erpgulf
```

Install adds the shared custom fields. **Country fields** (the Emirate field for the UAE, the Governorate field for Oman, the Buyer Reference for Belgium) are installed for every country that has a Company, so after creating or changing a Company's country run:

```bash
bench --site <site> migrate
```

Updating later: `cd apps/einvoice_erpgulf && git pull`, then `bench --site <site> migrate`, `bench build --app einvoice_erpgulf`, `bench --site <site> clear-cache` and restart the workers.

No Python packages beyond what Frappe already ships (`requests`, `PyJWT`). The site must be reachable from the internet on HTTPS for webhooks, and the scheduler must be enabled.

## Migrating from uae_erpgulf

Once, on each site that has `uae_erpgulf` installed. Data (settings, invoice fields, webhook logs, incoming invoices) is kept; the script renames the UAE-named doctypes and fields in place and hands them to the new app before the old one is uninstalled.

```bash
cd frappe-bench
bench get-app https://github.com/ERPGulf/einvoice_erpgulf.git
bench --site <site> set-maintenance-mode on
bench --site <site> execute einvoice_erpgulf.migrate_from_uae_erpgulf.run
bench --site <site> uninstall-app uae_erpgulf --no-backup --force
bench --site <site> install-app einvoice_erpgulf
bench build --app einvoice_erpgulf
bench --site <site> set-maintenance-mode off
sudo supervisorctl restart <bench-group>:*
```

Take a backup first and try it on a copy of the site. On Frappe Cloud the same steps run from the bench console. What is renamed: `UAE Incoming Invoices → E-Invoice Incoming Invoice`, `UAE E-Invoice Webhook Logs → E-Invoice Webhook Log`, `UAE E-invoicing success log → E-Invoice Success Log`; fields `custom_uae_einvoice_enabled → custom_einvoice_enabled`, `custom_accredited_service_providers → custom_einvoice_provider`, `custom_uae_einvoice_status → custom_einvoice_status`, `custom_uae_einvoice_status_notification → custom_einvoice_status_notification`, `custom_return_against_for_uae_einvoice → custom_return_against_for_einvoice`, `custom_submit_to_fta → custom_submit_to_authority`. Reports, print formats or scripts of your own that mention the old names need the same find-and-replace.

## Quick start (five minutes to a first submission)

1. **Company** → set *Country*, tick *E-Invoice Enabled*, pick the provider under *E-Invoice Provider*, make sure *Tax ID* holds the national tax number (the 15-digit TRN in the UAE).
2. **E-Invoice Provider Settings** → New: Company, Provider (same name as on Company), *Enabled*, credentials from the provider's sandbox, *Base URL*, *Participant ID* (Peppol ID). Save, then **E-Invoicing → Verify Token**.
3. **Customer / Supplier** → *Peppol ID* and the country's registration fields; their **Address** needs *Country* and, where the country requires it, the region (Emirate, Governorate).
4. **Sales Invoice** → fill *Tax Category* (and any dependent country fields), submit. The *E-Invoice Response* dialog shows the provider's reply; *E-Invoice Status*, *Reporting Status* and *Document ID* are filled in. Within five minutes the corner ribbon turns to *Reported* and the XML/PDF appear as attachments.

## Folder structure

```
einvoice_erpgulf/einvoice_erpgulf/
├── hooks.py                       # einvoice_countries, einvoice_providers, doc_events, scheduler
├── core/                          # country-neutral, provider-neutral
│   ├── base.py                    # BaseAdapter: the provider contract (+ build_ubl)
│   ├── registry.py                # get_profile(), get_adapter(), registered_providers(), sync_provider_options()
│   ├── setup.py                   # after_install / after_migrate: country fields + dropdowns
│   ├── numbers.py  codes.py       # decimals; ISO countries, UN/ECE units, type codes, label parsing
│   ├── standards/
│   │   ├── __init__.py            # BaseProfile (the country-profile contract)
│   │   ├── pint/                  # Peppol PINT defaults
│   │   ├── en16931/               # Peppol BIS 3.0 / EN 16931 defaults
│   │   └── ubl/builder.py         # UBL 2.1 Invoice / CreditNote from an ERPNext invoice
│   ├── submit_sales.py  send_purchase.py  status_sync.py  attach.py  webhook.py
│   ├── incoming.py  verify_token.py  validation.py  provider_settings.py  return_validation.py
├── countries/
│   ├── ae/  profile.py  fixtures/  providers/flick  marmin  suntech  (fynamics)
│   ├── om/  profile.py  fixtures/  providers/
│   └── be/  profile.py  fixtures/  providers/
├── e_invoice/                     # the Frappe module: doctypes, dashboard page, reports, workspace
├── fixtures/custom_field.json     # shared custom fields
├── public/js/                     # form scripts: buttons, ribbon, tooltips, bulk submit
└── migrate_from_uae_erpgulf.py    # one-off site migration
```

## Scheduled jobs

| Job | Schedule | What it does |
| --- | --- | --- |
| `core.status_sync.sync_pending_invoices` | every 5 min | For submitted invoices of the last 7 days with a Document ID that are not yet final, or still missing XML/PDF: ask the provider for status, update *Reporting Status*, attach documents |
| `core.webhook.update_webhook_logs` | every minute | Refresh *Last Webhook Logs Response* on every enabled Provider Settings row that has a webhook UUID |

Both need the scheduler enabled on the site (`bench --site <site> scheduler enable`).

## Webhooks

Each provider has its own listener inside its adapter file, registered as a guest-accessible endpoint, for example `/api/method/einvoice_erpgulf.countries.ae.providers.suntech.adapter.suntech_webhook_listener`. The Provider Settings form shows the URL to give the provider. Every delivery is logged in **E-Invoice Webhook Log**; missed webhooks are covered by the 5-minute scheduler. Never ask a provider to send `Authorization: Bearer` to a webhook — Frappe intercepts it; use a custom header.

## Development

```bash
cd apps/einvoice_erpgulf
pre-commit install          # ruff + ruff-format + prettier, as configured in .pre-commit-config.yaml
pre-commit run --all-files
```

Adding a provider is one new folder under `countries/<cc>/providers/` plus one line in `hooks.py`; adding a country is one `profile.py` plus one line. The [Developer Guide](docs/DEVELOPER_GUIDE.md) walks through both with a skeleton, and the [Testing Guide](docs/TESTING_GUIDE.md) section 9 is the acceptance checklist. Open a pull request on [GitHub](https://github.com/ERPGulf/einvoice_erpgulf); we review and merge adapters that pass that checklist on the provider's sandbox. Security reports to support@erpgulf.com, please not as public issues.

## Other ERPGulf e-invoicing apps

Clearance-model countries with a single government endpoint and no provider to choose are separate apps: [ZATCA (Saudi Arabia)](https://github.com/ERPGulf/zatca_erpgulf) · Malaysia (MyInvois) · more at [www.erpgulf.com](https://www.erpgulf.com)
