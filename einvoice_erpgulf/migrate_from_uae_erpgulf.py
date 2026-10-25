"""One-off migration of a site from uae_erpgulf to einvoice_erpgulf.

Run it ONCE, while uae_erpgulf is still installed and BEFORE installing
einvoice_erpgulf. Order on the bench:

    bench get-app https://github.com/ERPGulf/einvoice_erpgulf.git
    bench --site <site> set-maintenance-mode on
    bench --site <site> execute einvoice_erpgulf.migrate_from_uae_erpgulf.run
    bench --site <site> uninstall-app uae_erpgulf --no-backup --force
    bench --site <site> install-app einvoice_erpgulf
    bench --site <site> set-maintenance-mode off
    sudo supervisorctl restart <bench>:*

What it does (all inside one transaction):
  1. Creates the Module Def "E-Invoice" owned by einvoice_erpgulf.
  2. Renames the three UAE DocTypes (tables and rows kept) and moves them,
     plus E-Invoice Provider Settings, into the new module, so that
     uninstalling uae_erpgulf no longer deletes them.
  3. Renames the six custom fields whose names carried "uae" / "fta" (column
     renamed, values kept) and moves every uae_erpgulf Custom Field and
     Property Setter into the new module for the same reason.
  4. Re-points the Page, Reports and Workspace to the new module so the
     uninstall leaves them; install-app then overwrites them from the new JSON.
Everything is idempotent: running it twice is harmless.
"""

import frappe

OLD_APP = "uae_erpgulf"
OLD_MODULE = "uae_erpgulf"
NEW_APP = "einvoice_erpgulf"
NEW_MODULE = "E-Invoice"

DOCTYPE_RENAMES = {
	"UAE Incoming Invoices": "E-Invoice Incoming Invoice",
	"UAE E-Invoice Webhook Logs": "E-Invoice Webhook Log",
	"UAE E-invoicing success log": "E-Invoice Success Log",
}
MOVE_DOCTYPES = ["E-Invoice Provider Settings", *DOCTYPE_RENAMES.values()]

FIELD_RENAMES = [
	("Company", "custom_uae_einvoice_enabled", "custom_einvoice_enabled"),
	("Company", "custom_accredited_service_providers", "custom_einvoice_provider"),
	("Sales Invoice", "custom_uae_einvoice_status", "custom_einvoice_status"),
	("Purchase Invoice", "custom_uae_einvoice_status", "custom_einvoice_status"),
	("Sales Invoice", "custom_uae_einvoice_status_notification", "custom_einvoice_status_notification"),
	("Purchase Invoice", "custom_uae_einvoice_status_notification", "custom_einvoice_status_notification"),
	("Sales Invoice", "custom_return_against_for_uae_einvoice", "custom_return_against_for_einvoice"),
	("Purchase Invoice", "custom_submit_to_fta", "custom_submit_to_authority"),
]

PAGE_RENAMES = {"uae-e-invoice-dashbo": "e-invoice-dashboard"}
REPORT_RENAMES = {
	"UAE E-Invoice Sales Status Report": "E-Invoice Sales Status Report",
	"UAE E-Invoice Purchase Status Report": "E-Invoice Purchase Status Report",
}
WORKSPACE_RENAMES = {"UAE E-Invoice Integration": "E-Invoice"}


def run():
	if OLD_APP not in frappe.get_installed_apps():
		print(f"{OLD_APP} is not installed on this site; nothing to migrate.")
		return
	frappe.flags.in_migrate = True
	_module_def()
	_doctypes()
	_custom_fields()
	_property_setters()
	_desk_objects()
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	frappe.clear_cache()
	print("Done. Now: uninstall-app uae_erpgulf, then install-app einvoice_erpgulf.")


def _module_def():
	if not frappe.db.exists("Module Def", NEW_MODULE):
		frappe.get_doc({"doctype": "Module Def", "module_name": NEW_MODULE, "app_name": NEW_APP, "custom": 0}).insert(
			ignore_permissions=True
		)
		print(f"Module Def {NEW_MODULE} created")


def _doctypes():
	for old, new in DOCTYPE_RENAMES.items():
		if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
			frappe.rename_doc("DocType", old, new, force=True, ignore_permissions=True)
			print(f"DocType {old} -> {new}")
	for name in MOVE_DOCTYPES:
		if frappe.db.exists("DocType", name):
			frappe.db.set_value("DocType", name, "module", NEW_MODULE, update_modified=False)
			print(f"DocType {name} -> module {NEW_MODULE}")


def _custom_fields():
	from frappe.model.utils.rename_field import rename_field

	for dt, old, new in FIELD_RENAMES:
		old_cf = f"{dt}-{old}"
		if frappe.db.exists("Custom Field", old_cf) and not frappe.db.exists("Custom Field", f"{dt}-{new}"):
			frappe.db.set_value("Custom Field", old_cf, "fieldname", new, update_modified=False)
			frappe.rename_doc("Custom Field", old_cf, f"{dt}-{new}", force=True, ignore_permissions=True)
			frappe.clear_cache(doctype=dt)
			rename_field(dt, old, new)  # renames the column, keeps the data
			print(f"Custom Field {dt}.{old} -> {new}")
	# depends_on / mandatory_depends_on expressions that mention old names
	for cf in frappe.get_all("Custom Field", filters={"module": OLD_MODULE}, fields=["name", "depends_on", "mandatory_depends_on", "read_only_depends_on"]):
		updates = {}
		for key in ("depends_on", "mandatory_depends_on", "read_only_depends_on"):
			val = cf.get(key)
			if not val:
				continue
			new_val = val
			for _dt, old, new in FIELD_RENAMES:
				new_val = new_val.replace(old, new)
			if new_val != val:
				updates[key] = new_val
		if updates:
			frappe.db.set_value("Custom Field", cf.name, updates, update_modified=False)
	frappe.db.sql("update `tabCustom Field` set module=%s where module=%s", (NEW_MODULE, OLD_MODULE))
	print("Custom Fields moved to module", NEW_MODULE)


def _property_setters():
	frappe.db.sql("update `tabProperty Setter` set module=%s where module=%s", (NEW_MODULE, OLD_MODULE))


def _desk_objects():
	for doctype, renames in (("Page", PAGE_RENAMES), ("Report", REPORT_RENAMES), ("Workspace", WORKSPACE_RENAMES)):
		for old, new in renames.items():
			if frappe.db.exists(doctype, old) and not frappe.db.exists(doctype, new):
				frappe.rename_doc(doctype, old, new, force=True, ignore_permissions=True)
			if frappe.db.exists(doctype, new):
				frappe.db.set_value(doctype, new, "module", NEW_MODULE, update_modified=False)
	# anything else still owned by the old module (e.g. a custom report) follows along
	for doctype in ("Page", "Report", "Workspace", "Print Format", "Server Script", "Client Script"):
		if frappe.db.table_exists(doctype) and frappe.get_meta(doctype).has_field("module"):
			frappe.db.sql(f"update `tab{doctype}` set module=%s where module=%s", (NEW_MODULE, OLD_MODULE))
	print("Desk objects moved to module", NEW_MODULE)
