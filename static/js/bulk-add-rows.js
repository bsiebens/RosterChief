/*
 * Dynamic rows for the bulk-add formsets (team roster/staff, group members).
 *
 * Progressive enhancement: the formset's own `extra` rows are already in the
 * page and post normally with no JS at all -- this only adds the ability to
 * clone more rows from the hidden <template> holding the formset's empty_form,
 * and to drop a row you've changed your mind about.
 *
 * Removing a row deletes it outright and deliberately leaves TOTAL_FORMS alone
 * rather than re-indexing everything after it: Django reads a form whose fields
 * are simply absent from the POST as an unchanged extra form and skips it, so a
 * gap in the indices is harmless, while re-indexing live inputs is not.
 */
(() => {
    const rows = document.getElementById("bulk-add-rows");
    const template = document.getElementById("bulk-add-row-template");
    const addButton = document.getElementById("add-row");
    if (!rows || !template || !addButton) return;

    const totalForms = document.getElementById(`id_${rows.dataset.prefix}-TOTAL_FORMS`);
    if (!totalForms) return;

    // Opt-in second row list for the same formset, kept in lockstep by plain
    // DOM position rather than any index bookkeeping -- registration_status.html
    // is the only page with one (its own Lunch section mirrors this same
    // formset's rows one-for-one, see its own template comment for why). Every
    // other page using this script has neither attribute, so companionRows
    // stays null and every branch below is a no-op for them.
    const companionRows = rows.dataset.companionRows ? document.getElementById(rows.dataset.companionRows) : null;
    const companionTemplate = rows.dataset.companionTemplate ? document.getElementById(rows.dataset.companionTemplate) : null;

    // Jersey number and captaincy belong to a TeamMembership; a staff position
    // becomes a StaffAssignment, which has neither. PositionSelect
    // (management/forms.py) marks the staff options so those inputs can grey
    // themselves out to match -- and a disabled input isn't submitted, so the
    // server sees nothing set either way. The row form rejects them regardless;
    // this only saves the user from filling in something that can't apply.
    //
    // A tournament roster row (tournaments/forms.py's own role field) has no
    // Position model to read a staff flag off -- "[data-role-select]" is a
    // plain Player/Staff <select> instead, so "is this row staff" is just
    // its raw value rather than the chosen option's dataset. It's a data
    // attribute, not a class, because that row renders through the
    // form_field template tag, which duplicates a widget's own "class" attr
    // after its own hardcoded one -- a browser drops the second, so a plain
    // CSS class added there would never actually apply (see the form
    // field's own comment in tournaments/forms.py). "[data-player-only]"
    // is the same row's date-of-birth grey-out target for the same reason;
    // ".player-only" (a real class) still works for the team roster's own
    // bulk-add row below, rendered via plain {{ form.field }} instead.
    const syncPlayerOnlyFields = (row) => {
        const position = row.querySelector(".position-select");
        const role = row.querySelector("[data-role-select]");
        if (!position && !role) return;

        let isStaff = false;
        if (position) {
            const chosen = position.options[position.selectedIndex];
            isStaff = Boolean(chosen && chosen.dataset.staff === "1");
        } else {
            isStaff = role.value === "staff";
        }

        row.querySelectorAll(".player-only, [data-player-only]").forEach((field) => {
            field.disabled = isStaff;
            if (!isStaff) return;
            if (field.type === "checkbox") {
                field.checked = false;
            } else {
                field.value = "";
            }
        });
    };

    const wireRow = (row) => {
        row.querySelectorAll("select[data-searchable]").forEach((select) => {
            if (window.enhanceSearchableSelect) window.enhanceSearchableSelect(select);
        });

        const position = row.querySelector(".position-select");
        if (position) position.addEventListener("change", () => syncPlayerOnlyFields(row));
        const role = row.querySelector("[data-role-select]");
        if (role) role.addEventListener("change", () => syncPlayerOnlyFields(row));
        syncPlayerOnlyFields(row);

        const remove = row.querySelector(".remove-row");
        if (remove) {
            remove.addEventListener("click", () => {
                if (companionRows) {
                    const position = Array.from(rows.children).indexOf(row);
                    const companion = companionRows.children[position];
                    if (companion) companion.remove();
                }
                row.remove();
            });
        }
    };

    rows.querySelectorAll(".bulk-add-row").forEach(wireRow);

    // Cloned from the given template's parsed content rather than re-parsed
    // from its innerHTML: a formset row can be a <tr>, and assigning
    // "<tr>...</tr>" to some stray <div>'s innerHTML drops it on the floor --
    // a <tr> is only valid inside a table, which is exactly the context
    // <template> already parsed it in.
    const buildRow = (index, fromTemplate) => {
        const row = fromTemplate.content.firstElementChild.cloneNode(true);
        for (const element of [row, ...row.querySelectorAll("*")]) {
            for (const attribute of Array.from(element.attributes)) {
                if (attribute.value.includes("__prefix__")) {
                    attribute.value = attribute.value.replace(/__prefix__/g, index);
                }
            }
        }
        return row;
    };

    addButton.addEventListener("click", () => {
        const index = Number(totalForms.value);
        const row = buildRow(index, template);

        rows.appendChild(row);
        totalForms.value = index + 1;
        wireRow(row);

        if (companionRows && companionTemplate) {
            companionRows.appendChild(buildRow(index, companionTemplate));
        }

        const firstInput = row.querySelector("input, select");
        if (firstInput) firstInput.focus();
    });
})();
