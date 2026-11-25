"""Fetch the submitted XML / PDF from the provider and attach it to the invoice."""

import frappe
from frappe import _
from frappe.utils.file_manager import save_file
from frappe.utils.synchronization import filelock

from einvoice_erpgulf.core.permissions import load_invoice
from einvoice_erpgulf.core.provider_settings import get_active_provider_settings
from einvoice_erpgulf.core.registry import get_adapter



def _remove_same_name_files(doctype, name, file_prefix):
    """Delete earlier copies of this file on the invoice (including ones Frappe
    renamed with a hash suffix), so a fetch that runs twice never leaves duplicates."""
    for f in frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": doctype,
            "attached_to_name": name,
            "file_name": ["like", f"{file_prefix}%"],
        },
        pluck="name",
    ):
        frappe.delete_doc("File", f, force=1, ignore_permissions=True)

# ---- internal: no permission check (submit, scheduler, webhooks) ----
def attach_document_xml(doctype, invoice_name):
    try:
        with filelock(f"einvoice_xml_{doctype}_{invoice_name}", timeout=30):
            doc = frappe.get_doc(doctype, invoice_name)
            adapter = get_adapter(get_active_provider_settings(doc.company))
            xml_data = adapter.get_document_xml(doctype, doc)

            file_prefix = f"Submitted-XML-file {doc.name}"
            _remove_same_name_files(doctype, doc.name, file_prefix)

            file_doc = save_file(
                fname=f"{file_prefix}.xml",
                content=xml_data, dt=doctype, dn=doc.name, is_private=1,
            )
            doc.db_set("custom_document_xml", file_doc.file_url)
            frappe.db.commit()
        return {"status": "success", "file_url": file_doc.file_url}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "E-Invoice XML Fetch Error")
        frappe.throw(_("Failed to fetch document XML: {0}").format(str(e)))


def attach_document_pdf(doctype, invoice_name):
    try:
        with filelock(f"einvoice_pdf_{doctype}_{invoice_name}", timeout=30):
            doc = frappe.get_doc(doctype, invoice_name)
            adapter = get_adapter(get_active_provider_settings(doc.company))
            pdf_data = adapter.get_document_pdf(doctype, doc)

            file_prefix = f"Submitted-PDF-file {doc.name}"
            _remove_same_name_files(doctype, doc.name, file_prefix)

            file_doc = save_file(
                fname=f"{file_prefix}.pdf",
                content=pdf_data, dt=doctype, dn=doc.name, is_private=1,
            )
            doc.db_set("custom_document_pdf", file_doc.file_url)
            frappe.db.commit()
        return {"status": "success", "file_url": file_doc.file_url}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "E-Invoice PDF Fetch Error")
        frappe.throw(_("Failed to fetch document PDF: {0}").format(str(e)))


# ---- buttons: the user must be able to edit the invoice ----
@frappe.whitelist(methods=["POST"])
def get_document_xml(doctype: str, invoice_name: str):
    load_invoice(doctype, invoice_name, "write")
    return attach_document_xml(doctype, invoice_name)


@frappe.whitelist(methods=["POST"])
def get_document_pdf(doctype: str, invoice_name: str):
    load_invoice(doctype, invoice_name, "write")
    return attach_document_pdf(doctype, invoice_name)