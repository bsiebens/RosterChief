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
 * - Picking a team narrows "Jersey number" to that team's own pool
 *   (registration.services.pricing.team_number_pools, a json_script the
 *   same way) -- hidden entirely for a volunteer row, no team, or a team
 *   with no pool assigned. A row tied to an already-known person
 *   (existing_member) whose own current number in that pool is known
 *   (member-current-numbers, mobile re-registration only) gets that number
 *   pre-selected and labelled, rather than silently prefilled -- they can
 *   still pick a different one from the same list. Same UX-narrowing-only
 *   caveat as above; RegistrationEntryRowForm.clean is the real check.
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

    const teamPoolsBlob = document.getElementById("team-number-pools");
    const teamNumberPools = teamPoolsBlob ? JSON.parse(teamPoolsBlob.textContent) : {};
    const memberNumbersBlob = document.getElementById("member-current-numbers");
    const memberCurrentNumbers = memberNumbersBlob ? JSON.parse(memberNumbersBlob.textContent) : {};
    const yoursLabel = container.dataset.yoursLabel || "yours";

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

    const overlayInputFor = (select) => select.parentElement?.querySelector(":scope > input") || null;

    const syncJerseyNumberOptions = (row) => {
        if (!row) return;
        const kindSelect = row.querySelector('select[name$="-entry_kind"]');
        const teamSelect = row.querySelector('select[name$="-requested_team"]');
        const numberSelect = row.querySelector('select[name$="-requested_jersey_number"]');
        const numberField = row.querySelector(".jersey-number-field");
        if (!kindSelect || !teamSelect || !numberSelect) return;

        const numbers = kindSelect.value === "player" ? teamNumberPools[teamSelect.value] : undefined;

        if (!numbers) {
            numberField?.classList.add("hidden");
            if (numberSelect.value) {
                numberSelect.value = "";
                numberSelect.dispatchEvent(new Event("change"));
            }
            const overlay = overlayInputFor(numberSelect);
            if (overlay) overlay.value = "";
            return;
        }

        numberField?.classList.remove("hidden");

        const memberId = row.querySelector('input[name$="-existing_member"]')?.value || "";
        const currentNumber = memberId && memberCurrentNumbers[memberId] ? memberCurrentNumbers[memberId][teamSelect.value] : undefined;

        let selectedStillMatches = !numberSelect.value || numbers.includes(Number(numberSelect.value));
        Array.from(numberSelect.options).forEach((option) => {
            if (!option.value) return; // the blank/placeholder option -- always shown
            const value = Number(option.value);
            const isCurrent = currentNumber !== undefined && value === currentNumber;
            // The member's own current number stays offered even though
            // team_number_pools (computed with no specific member in mind)
            // otherwise marks it taken -- see that function's own docstring.
            option.hidden = !numbers.includes(value) && !isCurrent;
            option.text = isCurrent ? `${value} (${yoursLabel})` : String(value);
        });

        if (!numberSelect.value && currentNumber !== undefined) {
            numberSelect.value = String(currentNumber);
            selectedStillMatches = true;
        } else if (!selectedStillMatches) {
            numberSelect.value = "";
        }

        numberSelect.dispatchEvent(new Event("change"));
        const overlay = overlayInputFor(numberSelect);
        if (overlay) {
            const selected = Array.from(numberSelect.options).find((option) => option.value === numberSelect.value);
            overlay.value = selected && selected.value ? selected.text : "";
        }
    };

    const syncRow = (row) => {
        syncVariantOptions(row);
        syncJerseyNumberOptions(row);
    };

    container.querySelectorAll(".bulk-add-row").forEach(syncRow);

    container.addEventListener("change", (event) => {
        if (event.target.matches('input[name$="-is_contact"]') && event.target.checked) populateIdentity(event.target.closest(".bulk-add-row"));
        if (event.target.matches('select[name$="-entry_kind"]')) syncRow(event.target.closest(".bulk-add-row"));
        if (event.target.matches('select[name$="-requested_team"]')) syncJerseyNumberOptions(event.target.closest(".bulk-add-row"));
    });

    // A row added via "Add another person"/bulk-add-rows.js starts on
    // entry_kind's default ("player") -- give it the same filtered state a
    // fresh page load would, without bulk-add-rows.js needing to know
    // anything about this. Registered after bulk-add-rows.js's own listener
    // (script tag order), so its row has already been appended by the time
    // this runs.
    document.getElementById("add-row")?.addEventListener("click", () => {
        const rows = container.querySelectorAll(".bulk-add-row");
        if (rows.length) syncRow(rows[rows.length - 1]);
    });

    // Exposed for mobile-reregister-rows.js, which clones its own per-person
    // sub-rows outside of #bulk-add-rows entirely -- same filtering, one
    // implementation.
    window.syncRegistrationVariantOptions = syncVariantOptions;
    window.syncRegistrationJerseyOptions = syncJerseyNumberOptions;
})();
