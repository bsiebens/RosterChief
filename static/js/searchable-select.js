/*
 * Progressive enhancement for a plain <select data-searchable> (single or
 * `multiple`): a small typeahead combobox, a select2-alike without the
 * dependency. Type to filter, click or Enter to pick; the hidden <select>
 * still carries the actual value(s) the form submits, so this degrades to a
 * normal dropdown with no JS. A `multiple` select gets removable chips for
 * each pick instead of replacing its own value.
 *
 * Opt in with `data-searchable="true"` on the widget; `data-search-placeholder`
 * sets the search input's placeholder (see management/forms.py).
 */
(() => {
    function enhance(select) {
        const isMultiple = select.multiple;
        const options = Array.from(select.options).filter((option) => option.value !== "");

        const wrapper = document.createElement("div");
        wrapper.className = "relative";

        const chips = document.createElement("div");
        chips.className = "flex flex-wrap gap-1 empty:hidden mt-2";

        const input = document.createElement("input");
        input.type = "text";
        input.className = "input input-bordered w-full";
        input.placeholder = select.dataset.searchPlaceholder || "";
        input.autocomplete = "off";

        const list = document.createElement("ul");
        list.className = "menu absolute z-10 mt-1 w-full rounded-box bg-base-100 shadow max-h-60 overflow-y-auto flex-nowrap hidden";

        select.parentNode.insertBefore(wrapper, select);
        if (isMultiple) {
            // Chips render below the input, not above it: above meant every pick grew
            // the block ahead of the input and shoved it (and your cursor) down --
            // disorienting mid-search. Below, the input stays put; only the space
            // beneath it grows, and the open dropdown (absolutely positioned right
            // under the input) simply overlaps the chips while it's open.
            wrapper.append(input, list, chips, select);
        } else {
            wrapper.append(input, list, select);
        }
        select.classList.add("hidden");

        let highlighted = -1;

        const renderChips = () => {
            chips.innerHTML = "";
            options.filter((option) => option.selected).forEach((option) => {
                const chip = document.createElement("span");
                chip.className = "badge badge-neutral gap-1";
                chip.textContent = option.text;

                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "opacity-70 hover:opacity-100";
                remove.setAttribute("aria-label", "Remove");
                remove.textContent = "×";
                remove.addEventListener("mousedown", (event) => {
                    // mousedown, not click: it fires before the input's blur.
                    event.preventDefault();
                    option.selected = false;
                    renderChips();
                    // Only refresh the dropdown if it was already open (actively
                    // searching) -- removing a chip must never pop it open on its
                    // own, with no way to close it again short of a page reload.
                    if (!list.classList.contains("hidden")) {
                        render(input.value);
                    }
                    select.dispatchEvent(new Event("change"));
                });

                chip.appendChild(remove);
                chips.appendChild(chip);
            });
        };

        const choose = (option) => {
            if (isMultiple) {
                option.selected = true;
                renderChips();
                input.value = "";
                render("");
                input.focus();
            } else {
                select.value = option.value;
                input.value = option.text;
                list.classList.add("hidden");
            }
            select.dispatchEvent(new Event("change"));
        };

        const render = (query) => {
            const needle = query.trim().toLowerCase();
            const candidates = isMultiple ? options.filter((option) => !option.selected) : options;
            const matches = needle ? candidates.filter((option) => option.text.toLowerCase().includes(needle)) : candidates;

            list.innerHTML = "";
            matches.forEach((option) => {
                const item = document.createElement("li");
                const link = document.createElement("a");
                link.textContent = option.text;
                link.addEventListener("mousedown", (event) => {
                    event.preventDefault();
                    choose(option);
                });
                item.appendChild(link);
                list.appendChild(item);
            });

            highlighted = -1;
            list.classList.toggle("hidden", matches.length === 0);
            return matches;
        };

        input.addEventListener("input", () => render(input.value));
        input.addEventListener("focus", () => render(input.value));
        input.addEventListener("blur", () => list.classList.add("hidden"));

        input.addEventListener("keydown", (event) => {
            const items = Array.from(list.querySelectorAll("a"));
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (!items.length) return;
                highlighted = event.key === "ArrowDown" ? (highlighted + 1) % items.length : (highlighted - 1 + items.length) % items.length;
                items.forEach((item, index) => item.classList.toggle("menu-active", index === highlighted));
                items[highlighted].scrollIntoView({ block: "nearest" });
            } else if (event.key === "Enter" && highlighted >= 0 && items[highlighted]) {
                event.preventDefault();
                items[highlighted].dispatchEvent(new Event("mousedown"));
            } else if (event.key === "Escape") {
                list.classList.add("hidden");
            } else if (isMultiple && event.key === "Backspace" && input.value === "") {
                // Removes the most recently added chip, matching how tag inputs
                // elsewhere (e.g. Gmail's "To" field) treat Backspace on empty text.
                const selected = options.filter((option) => option.selected);
                if (selected.length) {
                    selected[selected.length - 1].selected = false;
                    renderChips();
                    render("");
                    select.dispatchEvent(new Event("change"));
                }
            }
        });

        if (isMultiple) {
            renderChips();
        } else if (select.value) {
            // Redisplayed after a validation error elsewhere in the form: keep
            // whatever was already chosen visible in the text input.
            const selected = options.find((option) => option.value === select.value);
            if (selected) input.value = selected.text;
        }
    }

    document.querySelectorAll("select[data-searchable]").forEach(enhance);
})();
