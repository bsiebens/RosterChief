/*
 * Shared behaviour for a registration entry formset row -- both the public
 * registration page (registration/templates/registration/register.html) and
 * mobile's re-registration screen (mobile/templates/mobile/reregister.html)
 * load this after searchable-select.js/bulk-add-rows.js. A single
 * change-event listener on the #bulk-add-rows container (not per-row) so a
 * row cloned later by bulk-add-rows.js is covered without needing its own
 * wiring.
 *
 * - Checking "This is me"/"Include this person" fills a row's name/date-of-
 *   birth fields from whoever is actually filling in the form -- the real
 *   Member record when signed in (data-known-* on #bulk-add-rows), otherwise
 *   whatever's currently typed into the contact fields above (read live, not
 *   a page-load snapshot). A row with no such fields (mobile's fixed-
 *   identity person rows) is simply left alone.
 * - "Registering as" narrows "Registering for" to just the variants whose
 *   product is tagged for that entry_kind (shop.models.ProductCategory.
 *   RegistrationKind, via registration.services.pricing.
 *   variant_registration_kinds, embedded as a json_script -- an
 *   uncategorised/ordinary product stays offered either way). Purely a UX
 *   narrowing -- RegistrationEntryRowForm.clean is what actually enforces
 *   the match server-side, so a stale value from before this script ran
 *   still gets a clear validation error rather than silently going through.
 */
(() => {
    const container = document.getElementById("bulk-add-rows");
    if (!container) return;

    const knownFirstName = container.dataset.knownFirstName;
    const knownLastName = container.dataset.knownLastName;
    const knownDateOfBirth = container.dataset.knownDateOfBirth || "";
    const contactFirstNameField = container.dataset.contactFirstNameId ? document.getElementById(container.dataset.contactFirstNameId) : null;
    const contactLastNameField = container.dataset.contactLastNameId ? document.getElementById(container.dataset.contactLastNameId) : null;

    const populateIdentity = (row) => {
        const firstName = knownFirstName || contactFirstNameField?.value || "";
        const lastName = knownLastName || contactLastNameField?.value || "";
        const firstNameField = row.querySelector('input[name$="-first_name"]');
        const lastNameField = row.querySelector('input[name$="-last_name"]');
        const dateOfBirthField = row.querySelector('input[name$="-date_of_birth"]');
        if (firstNameField && firstName) firstNameField.value = firstName;
        if (lastNameField && lastName) lastNameField.value = lastName;
        if (dateOfBirthField && knownDateOfBirth) dateOfBirthField.value = knownDateOfBirth;
    };

    const kindsBlob = document.getElementById("variant-registration-kinds");
    const variantKinds = kindsBlob ? JSON.parse(kindsBlob.textContent) : {};

    const syncVariantOptions = (row) => {
        const kindSelect = row.querySelector('select[name$="-entry_kind"]');
        const variantSelect = row.querySelector('select[name$="-product_variant"]');
        if (!kindSelect || !variantSelect) return;

        const kind = kindSelect.value;
        let selectedStillMatches = true;
        Array.from(variantSelect.options).forEach((option) => {
            if (!option.value) return; // the blank/placeholder option -- always shown
            const optionKind = variantKinds[option.value] || "";
            const matches = !optionKind || optionKind === kind;
            option.hidden = !matches;
            if (option.selected && !matches) selectedStillMatches = false;
        });

        if (!selectedStillMatches) {
            variantSelect.value = "";
            variantSelect.dispatchEvent(new Event("change"));
            // searchable-select.js redisplays the previous pick's text in its
            // own overlay input, a sibling of the (now hidden) native select --
            // clear that too, or the stale label would linger after the value
            // itself was reset.
            const overlayInput = variantSelect.parentElement?.querySelector(":scope > input");
            if (overlayInput) overlayInput.value = "";
        }
    };

    container.querySelectorAll(".bulk-add-row").forEach(syncVariantOptions);

    container.addEventListener("change", (event) => {
        if (event.target.matches('input[name$="-is_contact"]') && event.target.checked) populateIdentity(event.target.closest(".bulk-add-row"));
        if (event.target.matches('select[name$="-entry_kind"]')) syncVariantOptions(event.target.closest(".bulk-add-row"));
    });

    // A row added via "Add another person"/bulk-add-rows.js starts on
    // entry_kind's default ("player") -- give it the same filtered state a
    // fresh page load would, without bulk-add-rows.js needing to know
    // anything about this. Registered after bulk-add-rows.js's own listener
    // (script tag order), so its row has already been appended by the time
    // this runs.
    document.getElementById("add-row")?.addEventListener("click", () => {
        const rows = container.querySelectorAll(".bulk-add-row");
        if (rows.length) syncVariantOptions(rows[rows.length - 1]);
    });
})();
