"""Install / migrate hooks.

1. Shared custom fields come from fixtures/custom_field.json through the
   normal Frappe fixtures mechanism (module "E-Invoice").
2. Country fields (countries/<cc>/fixtures/custom_field.json) are installed
   here, only for countries that have at least one Company - a Belgian
   site never sees an Emirate field. Create the Company, then run
   ``bench --site <site> migrate`` (or Setup > E-Invoice > Install Country Fields).
3. Provider dropdowns are extended with every registered adapter name.
"""

import json
import os

import frappe

from einvoice_erpgulf.core.registry import _countries, get_profile_for_country, sync_provider_options


def _country_fixture_path(profile):
	module_path = os.path.dirname(frappe.get_module(profile.__class__.__module__).__file__)
	return os.path.join(module_path, "fixtures", "custom_field.json")


def countries_in_use():
	"""Country names (as on Company.country) that have a profile and a Company."""
	known = _countries()
	rows = frappe.get_all("Company", fields=["country"], distinct=True)
	return sorted({r.country for r in rows if r.country in known})


def sync_country_fields(countries=None):
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	countries = countries or countries_in_use()
	multi = len(countries) > 1
	for country in countries:
		profile = get_profile_for_country(country)
		path = _country_fixture_path(profile)
		if not os.path.exists(path):
			continue
		with open(path, encoding="utf-8") as f:  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-security-file-traversal
			entries = json.load(f)
		fields = {}
		for entry in entries:
			# On a multi-country site the Company-level labels stay generic.
			if multi and entry.get("dt") == "Company":
				continue
			entry = dict(entry)
			for key in ("doctype", "name", "modified", "creation", "owner", "modified_by", "docstatus", "idx"):
				entry.pop(key, None)
			dt = entry.pop("dt")
			fields.setdefault(dt, []).append(entry)
		if fields:
			create_custom_fields(fields, update=True)
	frappe.clear_cache()


@frappe.whitelist()
def install_country_fields():
	"""Button / bench execute entry point: install fields for every country in use."""
	frappe.only_for("System Manager")
	countries = countries_in_use()
	sync_country_fields(countries)
	return countries


def after_migrate():
	sync_country_fields()
	sync_provider_options()


after_install = after_migrate
