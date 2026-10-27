"""Country and provider registry.

Two dicts in hooks.py drive everything:

    einvoice_countries = {
        "United Arab Emirates": "einvoice_erpgulf.countries.ae.profile.AeProfile",
        ...
    }
    einvoice_providers = {
        "AE": {"Flick Network L.L.C": "einvoice_erpgulf.countries.ae.providers.flick.adapter.FlickAdapter", ...},
        "OM": {...},
        "BE": {...},
    }

The Company's ``country`` picks the profile; the Company's
``custom_einvoice_provider`` picks the adapter from that country's list.
Adding a country is a profile class plus one line in einvoice_countries;
adding a provider is an adapter class plus one line under its country. The
same adapter class may be listed under several countries.
"""

import frappe
from frappe import _

COMPANY_PROVIDER_FIELD = "custom_einvoice_provider"
COMPANY_ENABLED_FIELD = "custom_einvoice_enabled"
SETTINGS_DOCTYPE = "E-Invoice Provider Settings"


# ---------------------------------------------------------------- hooks plumbing
def _unwrap(value):
	"""frappe.get_hooks() wraps each dict value in a list so several apps can
	contribute; for our plain dicts take the last contribution."""
	if isinstance(value, list):
		return value[-1] if value else None
	return value


def _hook_dict(name):
	raw = frappe.get_hooks(name)
	merged = {}
	entries = raw if isinstance(raw, list) else [raw]
	for entry in entries:
		if isinstance(entry, dict):
			for key, value in entry.items():
				value = _unwrap(value)
				if value:
					merged[key] = value
	return merged


def _countries():
	"""{country name: profile dotted path}"""
	return _hook_dict("einvoice_countries")


def _providers():
	"""{country code: {provider name: adapter dotted path}}"""
	raw = _hook_dict("einvoice_providers")
	out = {}
	for cc, mapping in raw.items():
		if isinstance(mapping, dict):
			out[cc.upper()] = {k: _unwrap(v) for k, v in mapping.items() if _unwrap(v)}
	return out


# ---------------------------------------------------------------- profiles
def _profile_cache():
	if not hasattr(frappe.local, "einvoice_profiles"):
		frappe.local.einvoice_profiles = {}
	return frappe.local.einvoice_profiles


def get_profile_for_country(country_name):
	"""Profile instance for a country name as stored on Company.country."""
	if not country_name:
		frappe.throw(_("Set the Country on the Company before using e-invoicing."))
	cache = _profile_cache()
	if country_name in cache:
		return cache[country_name]
	path = _countries().get(country_name)
	if not path:
		frappe.throw(
			_("E-invoicing is not set up for {0}. Countries with a profile: {1}.").format(
				country_name, ", ".join(sorted(_countries())) or "none"
			)
		)
	profile = frappe.get_attr(path)()
	cache[country_name] = profile
	return profile


def get_profile(company):
	"""Profile instance for a Company (by name)."""
	country = frappe.db.get_value("Company", company, "country")
	return get_profile_for_country(country)


def country_code_of(company):
	return get_profile(company).country_code


@frappe.whitelist()
def get_ui_labels(company: str):
	"""Labels the forms need (authority short name, provider wording)."""
	try:
		return get_profile(company).ui_labels()
	except Exception:
		return {}


# ---------------------------------------------------------------- providers
@frappe.whitelist()
def registered_providers(country: str | None = None):
	"""Provider names with an adapter. With ``country`` (code or Company
	country name) only that country's; otherwise every registered name."""
	providers = _providers()
	if country:
		cc = country.upper() if len(country) == 2 else get_profile_for_country(country).country_code
		return sorted(providers.get(cc, {}).keys())
	names = set()
	for mapping in providers.values():
		names.update(mapping.keys())
	return sorted(names)


def providers_for_company(company):
	return registered_providers(country_code_of(company))


def assert_provider_registered(provider, company=None, country_code=None):
	"""Throw a clear message when a provider name has no adapter (for the
	company's country, when a company or country code is given)."""
	if country_code:
		names = registered_providers(country_code)
		where = _("for {0}").format(country_code)
	elif company:
		names = providers_for_company(company)
		where = _("for {0}").format(frappe.db.get_value("Company", company, "country"))
	else:
		names = registered_providers()
		where = ""
	if provider in names:
		return
	frappe.throw(
		_(
			"No adapter is registered for provider '{0}' {1}. Providers with an adapter: {2}. "
			"Add one to einvoice_providers in hooks.py (see docs/DEVELOPER_GUIDE.md)."
		).format(provider, where, ", ".join(names) or "none")
	)


def get_adapter(settings):
	"""settings: an E-Invoice Provider Settings doc. Returns an adapter
	instance bound to that row and to the company's country profile."""
	profile = get_profile(settings.company)
	dotted_path = _providers().get(profile.country_code, {}).get(settings.provider)
	if not dotted_path:
		frappe.throw(
			_("No adapter is registered for provider '{0}' in {1}. Add it to einvoice_providers in hooks.py.").format(
				settings.provider, profile.country_name
			)
		)
	adapter_class = frappe.get_attr(dotted_path)
	return adapter_class(settings, profile)


# ---------------------------------------------------------------- dropdown sync
def _merge_options(existing, names):
	lines = list((existing or "").split("\n"))
	missing = [n for n in names if n not in lines]
	if not missing:
		return None
	if lines and lines[0] != "":
		lines.insert(0, "")  # keep a blank first option
	return "\n".join(lines + missing)


def sync_provider_options():
	"""Make every provider registered in hooks.py selectable.

	Runs after install and after migrate. Appends any registered provider
	name missing from the Company provider field's options (a Custom Field
	owned by this app) and from the Provider field on E-Invoice Provider
	Settings (via a Property Setter, so the DocType JSON is untouched).
	Existing options are never removed, so a country's full official list
	stays. Adding a provider is the adapter folder, the hooks.py line, and
	bench migrate."""
	names = registered_providers()
	if not names:
		return

	cf_name = frappe.db.get_value("Custom Field", {"dt": "Company", "fieldname": COMPANY_PROVIDER_FIELD})
	if cf_name:
		cf = frappe.get_doc("Custom Field", cf_name)
		merged = _merge_options(cf.options, names)
		if merged is not None:
			cf.options = merged
			cf.save(ignore_permissions=True)

	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	df = frappe.get_meta(SETTINGS_DOCTYPE).get_field("provider")
	if df:
		merged = _merge_options(df.options, names)
		if merged is not None:
			make_property_setter(
				SETTINGS_DOCTYPE, "provider", "options", merged, "Text", validate_fields_for_doctype=False
			)

	frappe.clear_cache(doctype="Company")
	frappe.clear_cache(doctype=SETTINGS_DOCTYPE)
