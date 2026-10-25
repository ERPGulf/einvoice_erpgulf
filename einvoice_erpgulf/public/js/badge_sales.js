// Status corner ribbon on Sales Invoice (see einvoice_common.js).
frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        einvoice_render_status_ribbon(frm);
    },
});
