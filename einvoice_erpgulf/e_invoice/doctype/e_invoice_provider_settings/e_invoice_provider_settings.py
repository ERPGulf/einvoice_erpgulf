"""Controller for E-Invoice Provider Settings.
Once you've confirmed they match, disable or delete the Server Script -
having both a .py controller AND a Server Script doing the same
before_save/validate work means it would run twice, which is exactly the
kind of duplicate-side-effect bug this file is meant to avoid, not add.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class EInvoiceProviderSettings(Document):
	def validate(self):
		self.check_provider_registered()
		self.check_duplicate()
		self.check_auth_fields()
		self.enforce_single_enabled_environment()
		self.set_webhook_url()

	def check_auth_fields(self):
		if self.auth_type == "api_key" and not self.get_password("api_key", raise_exception=False):
			frappe.throw(_("Please enter the API Key for Auth Type api_key."))
		if self.auth_type == "oauth2" and not (self.client_id and self.get_password("client_secret", raise_exception=False)):
			frappe.throw(_("Please enter Client ID and Client Secret for Auth Type oauth2."))

	def check_provider_registered(self):
		"""A row can only be saved for a provider that has an adapter in
		hooks.py; otherwise every submit would fail later with a traceback."""
		if not self.provider:
			return
		from einvoice_erpgulf.core.registry import assert_provider_registered

		assert_provider_registered(self.provider, company=self.company)

	def check_duplicate(self):
		"""One row per company + provider + environment."""
		if not self.provider:
			return

		existing = frappe.db.exists(
			"E-Invoice Provider Settings",
			{
				"company": self.company,
				"provider": self.provider,
				"custom_environment": self.custom_environment,
				"name": ["!=", self.name or ""],
			},
		)
		if existing:
			frappe.throw(
				_(
					"An E-Invoice Provider Settings row already exists for {0} + {1} + {2} ({3})."
				).format(self.company, self.provider, self.custom_environment, existing)
			)

	def enforce_single_enabled_environment(self):
		"""Only one environment per company + provider can be enabled, so
		get_active_provider_settings always finds exactly one row."""
		if not self.enabled:
			return

		others = frappe.get_all(
			"E-Invoice Provider Settings",
			filters={
				"company": self.company,
				"provider": self.provider,
				"enabled": 1,
				"name": ["!=", self.name or ""],
			},
			pluck="name",
		)
		for name in others:
			frappe.db.set_value("E-Invoice Provider Settings", name, "enabled", 0)

		if others:
			frappe.msgprint(
				_("Disabled {0} so only {1} is active for {2}.").format(
					", ".join(others), self.custom_environment, self.provider
				),
				alert=True,
			)

	def set_webhook_url(self):
		"""Fills Webhook URL with whichever provider this row actually is'
		own listener endpoint - asks that adapter for it (get_webhook_
		listener_url(), providers/base.py) rather than hardcoding Flick's
		URL here unconditionally, which is what this used to do even for a
		Marmin row. An ASP with no webhook API at all (Marmin today, base
		class default) just leaves this field blank instead of showing a
		URL for a listener that doesn't exist for it."""
		from einvoice_erpgulf.core.registry import get_adapter

		try:
			url = get_adapter(self).get_webhook_listener_url()
		except Exception:
			url = None
		self.webhook_url = url or ""

	def on_update(self):
		"""More than one row can be enabled for the same company now (see
		validate() above - enforce_single_active_provider is gone), so this
		no longer pushes THIS row's own provider onto Company's Accredited
		Service Providers select field - doing that whenever any row's
		Enabled checkbox is ticked would silently overwrite whichever
		provider the company deliberately chose, every time a second or
		third row gets enabled for testing/history/comparison. Which
		provider is actually used for submission is Company's own
		Accredited Service Providers field, picked there directly (see
		get_active_provider_settings in provider_settings.py) - this file
		doesn't touch it any more.

		Still turns e-invoicing on for the company the first time any
		row here gets enabled, purely as a convenience default - it only
		gates whether e-invoicing is attempted at all
		(validate_accredited_service_provider in validation.py), not which
		provider handles it, so it's safe to leave on once set even while
		multiple rows are enabled."""
		if self.enabled:
			frappe.db.set_value("Company", self.company, "custom_einvoice_enabled", 1)