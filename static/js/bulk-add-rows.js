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

    // Jersey number and captaincy belong to a TeamMembership; a staff position
    // becomes a StaffAssignment, which has neither. PositionSelect
    // (management/forms.py) marks the staff options so those inputs can grey
    // themselves out to match -- and a disabled input isn't submitted, so the
    // server sees nothing set either way. The row form rejects them regardless;
    // this only saves the user from filling in something that can't apply.
    const syncPlayerOnlyFields = (row) => {
        const position = row.querySelector(".position-select");
        if (!position) return;

        const chosen = position.options[position.selectedIndex];
        const isStaff = Boolean(chosen && chosen.dataset.staff === "1");

        row.querySelectorAll(".player-only").forEach((field) => {
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
        syncPlayerOnlyFields(row);

        const remove = row.querySelector(".remove-row");
        if (remove) remove.addEventListener("click", () => row.remove());
    };

    rows.querySelectorAll(".bulk-add-row").forEach(wireRow);

    // Cloned from the template's parsed content rather than re-parsed from its
    // innerHTML: the rows are <tr>s, and assigning "<tr>...</tr>" to some stray
    // <div>'s innerHTML drops them on the floor -- a <tr> is only valid inside a
    // table, which is exactly the context <template> already parsed it in.
    const buildRow = (index) => {
        const row = template.content.firstElementChild.cloneNode(true);
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
        const row = buildRow(index);

        rows.appendChild(row);
        totalForms.value = index + 1;
        wireRow(row);

        const firstInput = row.querySelector("input, select");
        if (firstInput) firstInput.focus();
    });
})();
