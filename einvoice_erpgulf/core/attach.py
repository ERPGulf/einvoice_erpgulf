"""Fetch the submitted XML / PDF from the provider and attach it to the invoice."""

import frappe
from frappe import _
from frappe.utils.file_manager import save_file

from einvoice_erpgulf.core.permissions import load_invoice
from einvoice_erpgulf.core.provider_settings import get_active_provider_settings
from einvoice_erpgulf.core.registry import get_adapter


# ---- internal: no permission check (submit, scheduler, webhooks) ----
def attach_document_xml(doctype, invoice_name):
    try:
        doc = frappe.get_doc(doctype, invoice_name)
        adapter = get_adapter(get_active_provider_settings(doc.company))
        xml_data = adapter.get_document_xml(doctype, doc)

        if doc.custom_document_xml:
            for f in frappe.get_all("File", filters={"file_url": doc.custom_document_xml}, fields=["name"]):
                frappe.delete_doc("File", f.name, force=1)

        file_doc = save_file(
            fname=f"Submitted-XML-file {doc.name}.xml",
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
        doc = frappe.get_doc(doctype, invoice_name)
        adapter = get_adapter(get_active_provider_settings(doc.company))
        pdf_data = adapter.get_document_pdf(doctype, doc)

        if doc.custom_document_pdf:
            for f in frappe.get_all("File", filters={"file_url": doc.custom_document_pdf}, fields=["name"]):
                frappe.delete_doc("File", f.name, force=1)

        file_doc = save_file(
            fname=f"Submitted-PDF-file {doc.name}.pdf",
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
