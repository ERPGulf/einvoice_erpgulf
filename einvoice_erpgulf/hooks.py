app_name = "einvoice_erpgulf"
app_title = "einvoice_erpgulf"
app_publisher = "ERPGulf.com"
app_description = "E-Invoice app for ERPNext that can be adopted for any country from ERPGulf.com"
app_email = "support@erpgulf.com"
app_license = "mit"
required_apps = ["erpnext"]


# =====================================================================
# COUNTRIES
# =====================================================================
# One entry per country the app supports. The key is the country name
# exactly as it appears on Company > Country. The value is the profile
# class for that country: countries/<cc>/profile.py, which holds the
# national rules (Peppol identifier scheme, tax-ID rule, tax categories,
# region codes, currency decimals, customization IDs, labels for the UI).
#
# When a Company has this country, the app loads this profile and every
# provider adapter for that Company receives it as self.profile.
#
# To add a country:
#   1. write countries/<cc>/profile.py (subclass PintProfile or EN16931Profile)
#   2. add countries/<cc>/fixtures/custom_field.json for its own fields
#   3. add one line here
#   4. add an empty "<CC>": {} block under einvoice_providers below
#   5. bench --site <site> migrate   (installs the country fields for
#      every country that has a Company, and refreshes the dropdowns)
einvoice_countries = {
    "United Arab Emirates": "einvoice_erpgulf.countries.ae.profile.AeProfile",
    "Oman": "einvoice_erpgulf.countries.om.profile.OmProfile",
    "Belgium": "einvoice_erpgulf.countries.be.profile.BeProfile",
}


# =====================================================================
# PROVIDERS  (ASPs / Peppol access points / accredited platforms)
# =====================================================================
# Grouped by ISO country code. Inside each country: the provider name
# exactly as it must appear in the dropdowns (Company > E-Invoice
# Provider and E-Invoice Provider Settings > Provider) -> the adapter
# class that talks to that provider's API.
#
# A provider is selectable only if it is listed here; Company and
# Provider Settings refuse any other name. The dropdowns are extended
# automatically with every name below on bench migrate; nothing has to
# be edited in the DocType or the Custom Field.
#
# The same adapter class may be listed under more than one country when
# a provider is accredited in several (e.g. a UAE ASP also accredited in
# Oman): list it again under "OM", no new code needed.
#
# To add a provider:
#   1. write countries/<cc>/providers/<name>/adapter.py
#      (class subclassing einvoice_erpgulf.core.base.BaseAdapter)
#   2. add one line under its country here
#   3. bench --site <site> clear-cache && bench --site <site> migrate,
#      then restart the workers (hooks are cached in Redis)
einvoice_providers = {
    "AE": {
        "Flick Network L.L.C": "einvoice_erpgulf.countries.ae.providers.flick.adapter.FlickAdapter",
        "Marmin AI Software Design LLC": "einvoice_erpgulf.countries.ae.providers.marmin.adapter.MarminAdapter",
        "Suntech Business Solutions DMCC": "einvoice_erpgulf.countries.ae.providers.suntech.adapter.SuntechAdapter",
        # "Fynamics Techno Solutions FZCO": "einvoice_erpgulf.countries.ae.providers.fynamics.adapter.FynamicsAdapter",
    },
    "OM": {
        # Oman-accredited providers. A UAE adapter whose Oman API is the same
        # can be listed again here with the same class path.
    },
    "BE": {
        # Peppol access points for Belgium:
        # "Access Point Name": "einvoice_erpgulf.countries.be.providers.<name>.adapter.<Name>Adapter",
    },
}


# =====================================================================
# Install / migrate
# =====================================================================
# core/setup.py installs the country fields for every country that has a
# Company and appends every registered provider name to the dropdowns.
after_install = "einvoice_erpgulf.core.setup.after_install"
after_migrate = "einvoice_erpgulf.core.setup.after_migrate"


# =====================================================================
# Client scripts
# =====================================================================
doctype_js = {
    "Sales Invoice": [
        "public/js/einvoice_common.js",
        "public/js/sales_invoice.js",
        "public/js/tooltip.js",
        "public/js/badge_sales.js",
    ],
    "Purchase Invoice": [
        "public/js/einvoice_common.js",
        "public/js/purchase.js",
        "public/js/tooltip_pos.js",
        "public/js/badges_purchase.js",
    ],
    "E-Invoice Provider Settings": "public/js/e_invoice_provider_settings.js",
    "Supplier": "public/js/marmin_party.js",
}
doctype_list_js = {
    "Sales Invoice": "public/js/bulk_submit.js",
    "Purchase Invoice": "public/js/bulk_submit_pur.js",
}


# =====================================================================
# Document events
# =====================================================================
doc_events = {
    "Company": {
        "validate": "einvoice_erpgulf.core.validation.validate_company_provider",
    },
    "Sales Invoice": {
        "on_submit": "einvoice_erpgulf.core.submit_sales.generate_and_send_einvoice",
        "before_submit": "einvoice_erpgulf.core.validation.validate_accredited_service_provider",
        "before_cancel": "einvoice_erpgulf.core.validation.block_cancel_if_reported",
    },
    "Purchase Invoice": {
        "on_submit": "einvoice_erpgulf.core.send_purchase.generate_and_send_einvoice",
        "before_submit": "einvoice_erpgulf.core.validation.validate_accredited_service_provider",
        "before_cancel": "einvoice_erpgulf.core.validation.block_cancel_if_reported",
    },
}


# =====================================================================
# Fixtures: the shared (country-neutral) custom fields.
# Country-specific fields live in countries/<cc>/fixtures and are
# installed by core/setup.py, not by this list.
# =====================================================================
fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "E-Invoice"]]},
]


# =====================================================================
# Scheduler
# =====================================================================
scheduler_events = {
    "cron": {
        "* * * * *": [
            "einvoice_erpgulf.core.webhook.update_webhook_logs",
        ],
        "*/5 * * * *": [
            "einvoice_erpgulf.core.status_sync.sync_pending_invoices",
        ],
    }
}

import einvoice_erpgulf.core.return_validation  # noqa: E402,F401