// RosterChief member app -- registers the service worker and exposes
// window.rosterchiefPush.{subscribe,unsubscribe}() for M7 (Notifications)
// to wire a permission-request button to (browsers require a user gesture
// before Notification.requestPermission() will prompt, so this is never
// called automatically on page load).

if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/app/sw.js"));
}

function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = window.atob(base64);
    return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}

async function subscribe() {
    // Every early return here used to fail silently -- the "Enable" button's
    // own click handler flipped its card away regardless of the outcome, so
    // a missing VAPID key (nothing to configure client-side, purely a server
    // deployment gap) or a denied permission looked identical to success:
    // nothing happened, and nothing said why. console.warn at least gets it
    // into the browser console; notifications.html's own click handler is
    // what surfaces the user-visible failure state now.
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
        console.warn("rosterchiefPush: push notifications aren't supported in this browser.");
        return false;
    }

    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
        console.warn(`rosterchiefPush: notification permission was ${permission}, not granted.`);
        return false;
    }

    const publicKey = document.body.dataset.vapidPublicKey;
    if (!publicKey) {
        console.warn("rosterchiefPush: no VAPID public key configured on the server (DJANGO_VAPID_PUBLIC_KEY) -- push is not set up for this deployment.");
        return false;
    }

    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    const response = await fetch("/app/push/subscribe/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": document.body.dataset.csrftoken || "" },
        body: JSON.stringify(subscription.toJSON()),
    });
    if (!response.ok) {
        console.warn(`rosterchiefPush: the server rejected the subscription (${response.status}).`);
        return false;
    }
    return true;
}

async function unsubscribe() {
    if (!("serviceWorker" in navigator)) return;
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) return;

    await fetch("/app/push/subscribe/", {
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-CSRFToken": document.body.dataset.csrftoken || "" },
        body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    await subscription.unsubscribe();
}

// Whether *this* browser already holds an active push subscription -- a
// server-side PushSubscription row proves some device subscribed, not
// necessarily this one, so the "Enable" card (notifications.html) checks
// this on load rather than any server-side flag.
async function isSubscribed() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration) return false;
    const subscription = await registration.pushManager.getSubscription();
    return subscription !== null;
}

window.rosterchiefPush = { subscribe, unsubscribe, isSubscribed };

// ---------------------------------------------------------------------------
// App-feel behaviours (mirrors the iOS app): pull-to-refresh, refresh on
// return, network error toasts, re-tap active tab, app icon badge.
// All defensive: every feature no-ops if htmx or the expected DOM is absent.
// User-facing strings come from data-msg-* attributes on <body> (translated
// server-side); English fallbacks below only apply if those are missing.
// ---------------------------------------------------------------------------
(function () {
    "use strict";

    const msg = (key, fallback) => (document.body && document.body.dataset[key]) || fallback;
    const hasHtmx = () => typeof window.htmx !== "undefined" && typeof window.htmx.ajax === "function";

    // Only tab-root pages refresh: <main data-refreshable>. If no page opts in
    // at all (attribute absent everywhere), fall back to the whole document.
    function isRefreshable() {
        const main = document.querySelector("main");
        if (main && main.hasAttribute("data-refreshable")) return true;
        return false;
    }

    let refreshing = false;
    function refreshCurrent() {
        if (!hasHtmx() || refreshing) return Promise.resolve();
        refreshing = true;
        const scrollY = window.scrollY;
        return window
            .htmx.ajax("GET", location.pathname + location.search, { target: "body", swap: "innerHTML show:none", headers: { "HX-Boosted": "true" } })
            .catch(() => {})
            .finally(() => {
                refreshing = false;
                window.scrollTo(0, scrollY);
            });
    }
    window.rosterchiefRefresh = refreshCurrent;

    // (1) Pull to refresh -----------------------------------------------------
    const THRESHOLD = 70;
    let startY = null;
    let pull = 0;
    let ptr = null;

    function ensurePtr() {
        if (ptr && document.body.contains(ptr)) return ptr;
        ptr = document.createElement("div");
        ptr.className = "ptr";
        ptr.setAttribute("aria-hidden", "true");
        document.body.appendChild(ptr);
        return ptr;
    }
    function resetPtr() {
        if (ptr) {
            ptr.classList.remove("ptr-active");
            ptr.style.removeProperty("--ptr-pull");
            ptr.style.removeProperty("opacity");
        }
        startY = null;
        pull = 0;
    }

    document.addEventListener("touchstart", (e) => {
        if (e.touches.length !== 1 || window.scrollY > 0 || !isRefreshable() || refreshing) return;
        if (e.target.closest && e.target.closest("dialog, input, textarea, select, [data-no-ptr]")) return;
        startY = e.touches[0].clientY;
    }, { passive: true });

    document.addEventListener("touchmove", (e) => {
        if (startY === null) return;
        if (window.scrollY > 0) return resetPtr();
        pull = Math.max(0, (e.touches[0].clientY - startY) * 0.5);
        if (pull <= 0) return;
        const el = ensurePtr();
        el.style.setProperty("--ptr-pull", Math.min(pull, THRESHOLD * 1.5) + "px");
        el.style.opacity = String(Math.min(1, pull / THRESHOLD));
        el.classList.toggle("ptr-active", pull >= THRESHOLD);
    }, { passive: true });

    function endPull() {
        const triggered = startY !== null && pull >= THRESHOLD;
        if (triggered) {
            if (navigator.vibrate) try { navigator.vibrate(10); } catch (_) {}
            const el = ensurePtr();
            el.classList.add("ptr-active");
            refreshCurrent().finally(resetPtr);
        } else {
            resetPtr();
        }
    }
    document.addEventListener("touchend", endPull, { passive: true });
    document.addEventListener("touchcancel", resetPtr, { passive: true });

    // (2) Refetch when returning after >60s -----------------------------------
    let hiddenAt = null;
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") {
            hiddenAt = Date.now();
        } else if (hiddenAt !== null) {
            const away = Date.now() - hiddenAt;
            hiddenAt = null;
            if (away > 60000 && isRefreshable()) refreshCurrent();
        }
    });

    // (3) Error handling ------------------------------------------------------
    let toastEl = null;
    let toastTimer = null;
    function showToast(text, retry) {
        if (toastEl) toastEl.remove();
        clearTimeout(toastTimer);
        toastEl = document.createElement("div");
        toastEl.className = "toast";
        toastEl.setAttribute("role", "alert");
        const span = document.createElement("span");
        span.textContent = text;
        toastEl.appendChild(span);
        if (retry) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = msg("msgRetry", "Try again");
            btn.addEventListener("click", () => {
                toastEl.remove();
                retry();
            });
            toastEl.appendChild(btn);
        }
        document.body.appendChild(toastEl);
        // .toast starts hidden (opacity 0); .toast-show fades it in on the next frame.
        const shown = toastEl;
        requestAnimationFrame(() => shown.classList.add("toast-show"));
        toastTimer = setTimeout(() => toastEl && toastEl.remove(), 8000);
    }

    // Top banners -- the same strip Django messages render in base.html -- for htmx actions that
    // answer `HX-Trigger: {"rc:banner": {"level", "title", "body", "link": {"href", "label"}}}`.
    // Every banner (server-rendered or not) dismisses itself after a few seconds.
    const BANNER_ICONS = {
        success: '<path d="M21.801 10A10 10 0 1 1 17 3.335"/><path d="m9 11 3 3L22 4"/>',
        error: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
        warning: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
        info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    };
    const BANNER_CSS = { success: "alert-success", error: "alert-error", warning: "alert-warning", info: "alert-info" };
    const BANNER_MS = 5000;

    function dismissBanner(el) {
        if (!el || el.dataset.leaving) return;
        el.dataset.leaving = "1";
        el.style.transition = "opacity .25s ease, max-height .25s ease, padding .25s ease";
        el.style.overflow = "hidden";
        el.style.maxHeight = el.offsetHeight + "px";
        requestAnimationFrame(() => {
            el.style.opacity = "0";
            el.style.maxHeight = "0";
            el.style.paddingTop = "0";
            el.style.paddingBottom = "0";
        });
        setTimeout(() => {
            const wrap = el.parentElement;
            el.remove();
            if (wrap && wrap.hasAttribute("data-flash") && !wrap.children.length) wrap.remove();
        }, 300);
    }

    function armBanners() {
        document.querySelectorAll("[data-flash] .m-flash").forEach((el) => {
            if (el.dataset.armed) return;
            el.dataset.armed = "1";
            setTimeout(() => dismissBanner(el), BANNER_MS);
        });
    }

    function showBanner(spec) {
        const level = BANNER_CSS[spec.level] ? spec.level : "info";
        const inner = document.querySelector("main > div");
        if (!inner) return;
        let wrap = inner.querySelector(":scope > [data-flash]");
        if (!wrap) {
            wrap = document.createElement("div");
            wrap.className = "-mx-4 -mt-4 flex flex-col";
            wrap.setAttribute("data-flash", "");
            inner.prepend(wrap);
        }
        const el = document.createElement("div");
        el.className = "m-flash " + BANNER_CSS[level] + (wrap.children.length ? " border-t-2 border-white" : "");
        el.setAttribute("role", "alert");
        el.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + BANNER_ICONS[level] + "</svg>";
        const text = document.createElement("div");
        const title = document.createElement("div");
        title.className = "font-display text-sm font-bold tracking-wide uppercase";
        title.textContent = spec.title || "";
        const body = document.createElement("div");
        body.className = "text-sm";
        body.textContent = spec.body || "";
        text.append(title, body);
        if (spec.link && spec.link.href && String(spec.link.href).startsWith("/")) {
            const a = document.createElement("a");
            a.className = "mt-1 inline-block text-sm font-semibold underline";
            a.href = spec.link.href;
            a.textContent = spec.link.label || "";
            text.append(a);
        }
        el.append(text);
        wrap.append(el);
        window.scrollTo({ top: 0, behavior: "smooth" });
        armBanners();
    }

    document.body.addEventListener("rc:banner", (e) => {
        if (e.detail && typeof e.detail === "object") showBanner(e.detail.value && typeof e.detail.value === "object" ? e.detail.value : e.detail);
    });
    document.addEventListener("DOMContentLoaded", armBanners);
    document.addEventListener("htmx:afterSettle", armBanners);
    armBanners();

    // Server-sent toasts: a view answers an htmx request with `HX-Trigger: {"rc:toast": "text"}`
    // (see mobile.views.NotificationsView).
    document.body.addEventListener("rc:toast", (e) => {
        const text = e.detail && (e.detail.value || e.detail);
        if (typeof text === "string" && text) showToast(text);
    });

    function retryFor(detail) {
        const cfg = detail && detail.requestConfig;
        const method = cfg && cfg.verb ? String(cfg.verb).toUpperCase() : "GET";
        // Never silently replay a mutation; only GETs get a Retry button.
        if (method !== "GET" || !hasHtmx()) return null;
        const path = (cfg && cfg.path) || location.pathname + location.search;
        const target = (detail.target && detail.target.id && "#" + detail.target.id) || "body";
        const swap = target === "body" ? "innerHTML show:none" : undefined;
        return () => window.htmx.ajax("GET", path, { target, swap, headers: { "HX-Boosted": "true" } });
    }

    document.addEventListener("htmx:responseError", (e) => {
        const status = e.detail && e.detail.xhr ? e.detail.xhr.status : 0;
        if (status === 429) return showToast(msg("msgTooMany", "Too many attempts, try again later"));
        // Validation errors (4xx other than 408) are the form's own business.
        if (status >= 400 && status < 500 && status !== 408) return;
        showToast(msg("msgLoadFailed", "Couldn't load. Please try again."), retryFor(e.detail));
    });
    document.addEventListener("htmx:sendError", (e) => {
        const offline = navigator.onLine === false;
        showToast(offline ? msg("msgOffline", "You're offline. Check your connection.") : msg("msgLoadFailed", "Couldn't load. Please try again."), retryFor(e.detail));
    });
    window.addEventListener("online", () => toastEl && toastEl.remove());

    // (4) Re-tapping the active tab -------------------------------------------
    document.addEventListener("click", (e) => {
        const tab = e.target.closest && e.target.closest(".tab-bar-item-active");
        if (!tab) return;
        const url = new URL(tab.href, location.href);
        if (url.pathname !== location.pathname) return; // deeper page: normal navigation back to the root
        e.preventDefault();
        e.stopPropagation();
        const today = document.getElementById("today") || document.querySelector("[data-today]");
        if (today) {
            today.scrollIntoView({ behavior: "smooth", block: "start" });
        } else if (window.scrollY > 0) {
            window.scrollTo({ top: 0, behavior: "smooth" });
        } else if (tab.dataset.tab === "calendar" && !/[?&]today=1/.test(location.search)) {
            if (hasHtmx()) window.htmx.ajax("GET", url.pathname + "?today=1", { target: "body", swap: "innerHTML" });
        } else {
            refreshCurrent();
        }
    }, true);

    // (5) App icon badge ------------------------------------------------------
    function syncBadge() {
        if (!("setAppBadge" in navigator)) return;
        const el = document.querySelector("[data-unread]");
        const meta = document.querySelector('meta[name="unread-count"]');
        const raw = el ? el.dataset.unread : meta ? meta.content : null;
        if (raw === null || raw === undefined) return;
        const count = parseInt(raw, 10) || 0;
        try {
            if (count > 0) navigator.setAppBadge(count).catch(() => {});
            else if (navigator.clearAppBadge) navigator.clearAppBadge().catch(() => {});
        } catch (_) {}
    }
    document.addEventListener("DOMContentLoaded", syncBadge);
    document.addEventListener("htmx:afterSettle", syncBadge);
    document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && syncBadge());
})();

// Messages from the service worker: a notification click on an already-open window
// navigates in place; a push updates the unread badge without a reload.
(function () {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.addEventListener("message", function (event) {
        var m = event.data || {};
        if (m.type === "navigate" && typeof m.url === "string" && m.url.indexOf("/app/") === 0) {
            if (window.htmx) {
                window.htmx.ajax("GET", m.url, { target: "body", swap: "innerHTML", pushURL: true });
            } else {
                window.location.href = m.url;
            }
        }
        if (m.type === "unread" && typeof m.count === "number") {
            var el = document.querySelector("[data-unread]");
            if (el) el.setAttribute("data-unread", String(m.count));
            if (navigator.setAppBadge) {
                (m.count > 0 ? navigator.setAppBadge(m.count) : navigator.clearAppBadge()).catch(function () {});
            }
        }
    });
})();
