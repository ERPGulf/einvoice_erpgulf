// Status corner ribbon on Purchase Invoice (see einvoice_common.js).
frappe.ui.form.on("Purchase Invoice", {
    refresh(frm) {
        einvoice_render_status_ribbon(frm);
    },
});
