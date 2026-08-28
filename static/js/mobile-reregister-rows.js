/*
 * "Add another registration" per person on mobile's re-registration screen
 * (mobile/templates/mobile/reregister.html) -- a second team, or an
 * additional role (player *and* referee), added inline under that person's
 * own card rather than a separate entry point. Same clone-from-<template>/
 * __prefix__-replace idiom as bulk-add-rows.js, but with several
 * independent clone targets (one per person) instead of a single global
 * one -- bulk-add-rows.js's own fixed #bulk-add-rows/#add-row ids don't fit
 * that shape, so this is its own small script rather than a reuse.
 *
 * TOTAL_FORMS is shared across every person (and the "someone new" rows) --
 * same "a gap in indices is harmless" reasoning as bulk-add-rows.js's own
 * remove handling: removing a sub-row just drops it, no re-indexing.
 */
(() => {
    const template = document.getElementById("subrow-template");
    const totalForms = document.querySelector('input[name="entries-TOTAL_FORMS"]');
    if (!template || !totalForms) return;

    const wireRow = (row) => {
        row.querySelectorAll("select[data-searchable]").forEach((select) => {
            if (window.enhanceSearchableSelect) window.enhanceSearchableSelect(select);
        });
        // Matches this person's own entry_kind default ("player") against
        // the fresh row's still-empty product_variant choice -- nothing to
        // narrow yet, but keeps every row (cloned or not) in the same
        // filtered state once a choice is made.
        if (window.syncRegistrationVariantOptions) window.syncRegistrationVariantOptions(row);

        const remove = row.querySelector(".remove-row");
        if (remove) remove.addEventListener("click", () => row.remove());
    };

    document.querySelectorAll(".add-subrow").forEach((button) => {
        button.addEventListener("click", () => {
            const personId = button.dataset.person;
            const container = document.getElementById(`person-rows-${personId}`);
            if (!container) return;

            const index = Number(totalForms.value);
            const row = template.content.firstElementChild.cloneNode(true);
            for (const element of [row, ...row.querySelectorAll("*")]) {
                for (const attribute of Array.from(element.attributes)) {
                    if (attribute.value.includes("__prefix__")) {
                        attribute.value = attribute.value.replace(/__prefix__/g, index);
                    }
                }
            }
            const existingMemberField = row.querySelector('input[name$="-existing_member"]');
            if (existingMemberField) existingMemberField.value = personId;

            container.appendChild(row);
            totalForms.value = index + 1;
            wireRow(row);
        });
    });
})();
