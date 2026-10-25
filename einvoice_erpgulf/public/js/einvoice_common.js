// Shared UI helpers for Sales Invoice and Purchase Invoice forms.
//
// Labels that differ per country (the tax authority's short name, what the
// provider is called) come from the company's country profile through one
// whitelisted call, cached per company for the session.

window.einvoice_labels_cache = window.einvoice_labels_cache || {};

function einvoice_ui_labels(company) {
    const cache = window.einvoice_labels_cache;
    if (cache[company]) {
        return Promise.resolve(cache[company]);
    }
    return frappe
        .call({
            method: "einvoice_erpgulf.core.registry.get_ui_labels",
            args: { company: company },
        })
        .then((r) => {
            cache[company] = r.message || {};
            return cache[company];
        })
        .catch(() => ({}));
}

// Status corner ribbon, drawn into the HTML field
// custom_einvoice_status_notification. A CSS ribbon: identical size and
// position for every status, right-aligned in its own column and clipped to
// a fixed box so it never overlaps the sidebar.
function einvoice_render_status_ribbon(frm) {
    const field = "custom_einvoice_status_notification";
    if (!frm.get_field(field)) {
        return;
    }
    einvoice_ui_labels(frm.doc.company).then((labels) => {
        const authority = labels.authority || "Tax Authority";
        const provider = labels.provider_short || "Provider";
        const einvoice = (frm.doc.custom_einvoice_status || "").toUpperCase();
        const reporting = (frm.doc.custom_reporting_status || "").toUpperCase();

        // label, ribbon colour
        let ribbon = null;
        if (einvoice === "FAILED" || reporting === "FAILED" || reporting === "REJECTED") {
            ribbon = [reporting === "REJECTED" ? authority + " Rejected" : authority + " Failed", "#E0232E"];
        } else if (einvoice === "SUCCESS" && reporting === "REPORTED") {
            ribbon = [authority + " Reported", "#23B33A"];
        } else if (einvoice === "SUCCESS" && reporting === "PENDING") {
            ribbon = [authority + " Pending", "#F29D1C"];
        } else if (einvoice === "SUCCESS" && reporting === "NOT REPORTED") {
            ribbon = ["Not Reported", "#8A8F94"];
        } else if (einvoice === "SUCCESS") {
            ribbon = ["Sent to " + provider, "#2F6FD6"];
        }

        let html = " ";
        if (ribbon) {
            const [label, color] = ribbon;
            html =
                '<div style="display:flex;justify-content:flex-end">' +
                '<div style="position:relative;width:150px;height:150px;overflow:hidden;pointer-events:none">' +
                '<div style="position:absolute;top:34px;right:-52px;width:230px;transform:rotate(45deg);' +
                "background:" + color + ";color:#ffffff;font-weight:700;font-size:15px;letter-spacing:0.3px;" +
                'text-align:center;line-height:34px;box-shadow:0 2px 4px rgba(0,0,0,0.25);">' +
                frappe.utils.escape_html(label) +
                "</div>" +
                "</div></div>";
        }
        frm.set_df_property(field, "options", html);
        frm.refresh_field(field);
    });
}
