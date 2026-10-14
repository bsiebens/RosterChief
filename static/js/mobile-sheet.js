// RosterChief member app -- the event bottom sheet, the answer note panels, the Calendar's header
// menus and "jump to today". Plain delegated listeners (no Alpine): everything here has to keep
// working after htmx swaps the page body or the sheet's own content.
(function () {
    "use strict";

    // ---- Event sheet ---------------------------------------------------------
    // Any link to /app/events/<uuid>/ opens the event as a sheet over the current screen when the
    // page carries the sheet shell (Home, Calendar); elsewhere -- or with modifier keys, or without
    // JavaScript -- it is a normal link to the full event page (a push deep link lands there too).
    const EVENT_PATH = /^\/app\/events\/[0-9a-f-]{36}\/$/i;
    let lastUrl = null;

    const shell = () => document.getElementById("event-sheet");
    const content = () => document.getElementById("event-sheet-content");
    const fill = (id) => {
        const holder = content();
        const tpl = document.getElementById(id);
        if (holder && tpl) holder.replaceChildren(tpl.content.cloneNode(true));
    };

    async function loadSheet(url) {
        const holder = content();
        if (!holder) return;
        lastUrl = url;
        fill("event-sheet-loading");
        try {
            const response = await fetch(url + (url.includes("?") ? "&" : "?") + "sheet=1", {
                credentials: "same-origin",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            });
            if (response.redirected && !EVENT_PATH.test(new URL(response.url).pathname)) {
                // Signed out meanwhile: let the normal flow take over.
                window.location.href = response.url;
                return;
            }
            if (!response.ok) throw new Error(String(response.status));
            holder.innerHTML = await response.text();
            if (window.htmx) window.htmx.process(holder);
            const dialog = shell();
            if (dialog) dialog.scrollTop = 0;
        } catch (_) {
            fill("event-sheet-error");
        }
    }

    function openSheet(url) {
        const dialog = shell();
        if (!dialog || typeof dialog.showModal !== "function") return false;
        dialog.dataset.dirty = "";
        if (!dialog.open) dialog.showModal();
        // Start on the sheet itself, not its first link (an auto-focused row drew a stray focus outline).
        dialog.setAttribute("tabindex", "-1");
        dialog.focus({ preventScroll: true });
        loadSheet(url);
        return true;
    }
    window.rosterchiefOpenEvent = openSheet;

    document.addEventListener(
        "click",
        (e) => {
            if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            const target = e.target instanceof Element ? e.target : null;
            if (!target) return;

            const closer = target.closest("[data-sheet-close]");
            if (closer && shell() && shell().contains(closer)) {
                shell().close();
                return;
            }
            if (target.closest("[data-sheet-retry]") && lastUrl) {
                loadSheet(lastUrl);
                return;
            }
            // A tap on the backdrop (the dialog element itself, outside its padding box) closes it.
            if (target === shell() && shell().open) {
                const box = target.getBoundingClientRect();
                if (e.clientY < box.top || e.clientX < box.left || e.clientX > box.right) shell().close();
                return;
            }

            const link = target.closest("a[href]");
            if (!link || link.hasAttribute("download") || link.target === "_blank" || link.hasAttribute("data-no-sheet")) return;
            let url;
            try {
                url = new URL(link.href, location.href);
            } catch (_) {
                return;
            }
            if (url.origin !== location.origin || !EVENT_PATH.test(url.pathname) || !shell()) return;
            if (shell().contains(link)) return;
            e.preventDefault();
            e.stopPropagation();
            openSheet(url.pathname);
        },
        true,
    );

    // Something was answered inside the sheet: refresh the screen underneath once it closes.
    document.addEventListener("htmx:afterRequest", (e) => {
        const dialog = shell();
        const detail = e.detail || {};
        if (dialog && dialog.open && detail.elt && dialog.contains(detail.elt) && detail.successful && detail.requestConfig && String(detail.requestConfig.verb).toLowerCase() === "post") {
            dialog.dataset.dirty = "1";
        }
    });
    document.addEventListener(
        "close",
        (e) => {
            if (e.target && e.target.id === "event-sheet" && e.target.dataset.dirty === "1") {
                e.target.dataset.dirty = "";
                if (window.rosterchiefRefresh) window.rosterchiefRefresh();
            }
        },
        true,
    );

    // Drag the handle down to dismiss.
    let drag = null;
    document.addEventListener(
        "touchstart",
        (e) => {
            const handle = e.target.closest && e.target.closest("[data-sheet-handle]");
            if (handle && e.touches.length === 1) drag = { y: e.touches[0].clientY, dy: 0 };
        },
        { passive: true },
    );
    document.addEventListener(
        "touchmove",
        (e) => {
            if (!drag) return;
            drag.dy = Math.max(0, e.touches[0].clientY - drag.y);
            const dialog = shell();
            if (dialog) dialog.style.transform = `translateY(${drag.dy}px)`;
        },
        { passive: true },
    );
    function endDrag() {
        if (!drag) return;
        const dialog = shell();
        const far = drag.dy > 100;
        drag = null;
        if (!dialog) return;
        dialog.style.transform = "";
        if (far) dialog.close();
    }
    document.addEventListener("touchend", endDrag, { passive: true });
    document.addEventListener("touchcancel", endDrag, { passive: true });

    // ---- Note panels -----------------------------------------------------------
    // <form data-note-form> holds the answer buttons ([data-note-hide]) and a hidden
    // <fieldset data-note-panel disabled> (reason textarea + Cancel/Send). A [data-ask] button
    // swaps them; [data-status] picks the status the panel submits, and the textarea is only
    // required for Out. A disabled fieldset is neither validated nor submitted, so In still works.
    function setPanel(form, open, status) {
        const panel = form.querySelector("[data-note-panel]");
        if (!panel) return;
        form.querySelectorAll("[data-note-hide]").forEach((el) => {
            el.hidden = open;
        });
        panel.hidden = !open;
        panel.disabled = !open;
        const statusInput = panel.querySelector("[data-note-status]");
        if (statusInput && open && status) statusInput.value = status;
        panel.querySelectorAll("[data-for-status]").forEach((el) => {
            el.hidden = !(open && el.dataset.forStatus === status);
        });
        const text = panel.querySelector("textarea");
        if (text) {
            const needed = !statusInput || status === "absent";
            text.required = open && needed;
            if (needed) text.minLength = 2;
            if (open) text.focus({ preventScroll: true });
        }
    }
    document.addEventListener("click", (e) => {
        const target = e.target instanceof Element ? e.target : null;
        if (!target) return;
        const ask = target.closest("[data-ask]");
        if (ask) {
            const form = ask.closest("[data-note-form]");
            if (form) setPanel(form, true, ask.dataset.status || "");
            return;
        }
        const cancel = target.closest("[data-ask-cancel]");
        if (cancel) {
            const form = cancel.closest("[data-note-form]");
            if (form) setPanel(form, false, "");
        }
    });

    // ---- Header menus (<details data-menu>): one open at a time, closed by an outside tap ----
    document.addEventListener("click", (e) => {
        const inside = e.target instanceof Element ? e.target.closest("details[data-menu]") : null;
        document.querySelectorAll("details[data-menu][open]").forEach((menu) => {
            if (menu !== inside) menu.removeAttribute("open");
        });
    });

    // ---- Jump to today (Calendar list; ?today=1 or after "show earlier") ----------
    function jumpToToday() {
        const flagged = document.querySelector("[data-scroll-today]");
        const today = document.getElementById("today");
        if (flagged && today) today.scrollIntoView({ block: "start" });
    }
    document.addEventListener("DOMContentLoaded", jumpToToday);
    document.addEventListener("htmx:afterSettle", jumpToToday);
})();
