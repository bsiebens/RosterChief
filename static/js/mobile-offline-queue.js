/*
 * Offline-tolerant coach entry (attendance check-ins, the attendance edit sheet,
 * line-up picks), loaded by mobile/templates/mobile/coach/base.html only.
 *
 * What it does
 *  1. Every attendance/line-up mutation htmx sends carries an Idempotency-Key header
 *     (api.app.idempotency on the server; the same store the native apps use). A key is
 *     kept across manual retries of the same action (same URL + same fields) until one
 *     attempt reaches the server, so a response lost in a tunnel never double-applies --
 *     Publish especially (it notifies players).
 *  2. When the network is down (htmx:sendError), a queueable mutation is stored in
 *     IndexedDB with its key and replayed in order when the connection returns (online
 *     event, tab becoming visible, Background Sync via the service worker, or a timer).
 *     The inline status line says "Saved offline, will sync". A replay the server
 *     refuses (the event was cancelled, the answer is no longer editable, the session
 *     ended...) stops the queue and asks Discard or Retry.
 *     NEVER queued: Publish/schedule and "Remind" -- they notify people and need a live
 *     confirmation, so they show the offline error instead.
 *
 * Security
 *  - The queue lives in this origin's IndexedDB only (each club is its own subdomain,
 *    so clubs never share it). Entries are tagged with the signed-in user id; entries of
 *    anyone else are purged on load and never replayed. The service worker deletes the
 *    whole database when it sees the sign-out POST, and a "signed-out" message clears it
 *    from an open page.
 *  - CSRF: the token is NOT trusted from queue time. At replay the header is taken from
 *    the current page (data-csrftoken on <body>); Django's CSRF secret only changes on
 *    login/logout, so a token from the same session stays valid, and a 403 simply puts
 *    the entry in the Discard/Retry state instead of dropping it. The stored body has its
 *    csrfmiddlewaretoken field removed.
 *  - Only the form fields the coach typed are stored (a status and, for the edit sheet,
 *    the private coach note). Nothing is read from or written to localStorage.
 */
(function () {
    "use strict";

    // A boosted navigation re-evaluates scripts in the swapped body: wire the listeners once per document.
    if (window.__rcOfflineQueue) {
        window.__rcOfflineQueue.flush();
        return;
    }

    const script = document.currentScript;
    const cfg = (script && script.dataset) || {};
    const USER = cfg.user || "";
    const msg = (key, fallback) => cfg[key] || fallback;

    const DB_NAME = "rosterchief-offline";
    const STORE = "queue";
    const SYNC_TAG = "rc-coach-queue";

    const QUEUEABLE = [/^\/app\/coach\/attendance\/[0-9a-f-]+\/[0-9a-f-]+\/(check-in|edit)\/$/, /^\/app\/coach\/lineup\/[0-9a-f-]+\/players\/[0-9a-f-]+\/$/];
    const LIVE_ONLY = [/^\/app\/coach\/lineup\/[0-9a-f-]+\/publish\/$/, /^\/app\/coach\/attendance\/[0-9a-f-]+\/remind-silent\/$/];

    const pathOf = (path) => {
        try {
            return new URL(path, location.href).pathname;
        } catch (_) {
            return "";
        }
    };
    const isQueueable = (path) => QUEUEABLE.some((re) => re.test(pathOf(path)));
    const isLiveOnly = (path) => LIVE_ONLY.some((re) => re.test(pathOf(path)));

    function uuid() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    }

    // ---- IndexedDB (promise wrappers; every failure degrades to "no queue") ----------------
    let dbPromise = null;
    function openDb() {
        if (dbPromise) return dbPromise;
        dbPromise = new Promise((resolve, reject) => {
            if (!window.indexedDB) return reject(new Error("no indexedDB"));
            const req = indexedDB.open(DB_NAME, 1);
            req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "seq", autoIncrement: true });
            req.onsuccess = () => {
                const db = req.result;
                // Let the service worker's deleteDatabase() (sign-out) through.
                db.onversionchange = () => {
                    db.close();
                    dbPromise = null;
                };
                resolve(db);
            };
            req.onerror = () => reject(req.error);
        });
        dbPromise.catch(() => {
            dbPromise = null;
        });
        return dbPromise;
    }
    function tx(mode, work) {
        return openDb().then(
            (db) =>
                new Promise((resolve, reject) => {
                    const t = db.transaction(STORE, mode);
                    const result = work(t.objectStore(STORE));
                    t.oncomplete = () => resolve(result && "result" in result ? result.result : undefined);
                    t.onerror = () => reject(t.error);
                    t.onabort = () => reject(t.error);
                }),
        );
    }
    const putEntry = (entry) => tx("readwrite", (s) => s.put(entry));
    const deleteEntry = (seq) => tx("readwrite", (s) => s.delete(seq));
    const allEntries = () => tx("readonly", (s) => s.getAll()).then((rows) => (rows || []).sort((a, b) => a.seq - b.seq));
    const clearAll = () => tx("readwrite", (s) => s.clear());

    // ---- feedback -------------------------------------------------------------------------
    function setStatus(text) {
        const el = document.getElementById("inline-status");
        if (el) el.textContent = text;
    }
    function toast(kind, title) {
        const host = document.getElementById("inline-toast") || document.body;
        const el = document.createElement("div");
        el.className = "toast toast-show " + (kind === "error" ? "toast-error" : "toast-ok");
        el.setAttribute("role", kind === "error" ? "alert" : "status");
        const strong = document.createElement("strong");
        strong.className = "font-semibold";
        strong.textContent = title;
        el.appendChild(strong);
        host.appendChild(el);
        setTimeout(() => el.remove(), 4000);
    }
    const fmt = (template, n) => template.replace("%(count)d", String(n));

    async function refreshStatus() {
        let rows = [];
        try {
            rows = (await allEntries()).filter((r) => r.user === USER);
        } catch (_) {
            return;
        }
        const pending = rows.filter((r) => r.state !== "failed").length;
        if (pending) setStatus(fmt(msg("msgQueued", "Saved offline. Will sync (%(count)d)"), pending));
        renderPanel(rows.find((r) => r.state === "failed"));
    }

    function renderPanel(failed) {
        let panel = document.getElementById("rc-offline-panel");
        if (!failed) {
            if (panel) panel.remove();
            return;
        }
        if (!panel) {
            panel = document.createElement("div");
            panel.id = "rc-offline-panel";
            panel.setAttribute("role", "alert");
            panel.style.cssText = "position:fixed;left:12px;right:12px;bottom:84px;z-index:60;padding:12px;border-radius:12px;background:#fff;color:#111;border:1px solid #b91c1c;box-shadow:0 4px 16px rgba(0,0,0,.25);font:500 14px system-ui,sans-serif";
            document.body.appendChild(panel);
        }
        panel.textContent = "";
        const text = document.createElement("p");
        text.style.cssText = "margin:0 0 10px";
        text.textContent = failed.error || msg("msgFailed", "A change saved offline could not be synced.");
        const row = document.createElement("div");
        row.style.cssText = "display:flex;gap:8px";
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "btn-rc btn-rc-primary";
        retry.textContent = msg("msgRetry", "Retry");
        retry.addEventListener("click", async () => {
            failed.state = "pending";
            failed.error = "";
            await putEntry(failed).catch(() => {});
            flush();
        });
        const discard = document.createElement("button");
        discard.type = "button";
        discard.className = "btn-rc btn-rc-neutral";
        discard.textContent = msg("msgDiscard", "Discard");
        discard.addEventListener("click", async () => {
            await deleteEntry(failed.seq).catch(() => {});
            await refreshStatus();
            flush();
        });
        row.append(retry, discard);
        panel.append(text, row);
    }

    // ---- 1. Idempotency-Key on every attendance / line-up mutation ---------------------------
    const keys = new Map(); // signature -> key, kept until an attempt reaches the server
    const inflight = new WeakMap(); // triggering element -> { sig, key, path, pairs }

    function pairsOf(detail) {
        const out = [];
        const source = detail.formData instanceof FormData ? detail.formData : null;
        if (source) {
            source.forEach((value, name) => {
                if (typeof value === "string") out.push([name, value]);
            });
        } else if (detail.parameters) {
            Object.keys(detail.parameters).forEach((name) => out.push([name, String(detail.parameters[name])]));
        }
        return out.filter(([name]) => name !== "csrfmiddlewaretoken");
    }

    document.addEventListener("htmx:configRequest", (event) => {
        const detail = event.detail;
        if (String(detail.verb).toLowerCase() !== "post") return;
        if (!isQueueable(detail.path) && !isLiveOnly(detail.path)) return;
        const pairs = pairsOf(detail);
        const sig = detail.path + "|" + JSON.stringify(pairs);
        let key = keys.get(sig);
        if (!key) {
            key = uuid();
            keys.set(sig, key);
        }
        detail.headers["Idempotency-Key"] = key;
        inflight.set(event.target, { sig, key, path: detail.path, pairs });
    });

    // An answer from the server (even an error) means the attempt arrived: the key is spent.
    document.addEventListener("htmx:afterRequest", (event) => {
        const info = inflight.get(event.target);
        if (info && event.detail.xhr && event.detail.xhr.status > 0) keys.delete(info.sig);
    });

    // ---- 2. queue on network failure -----------------------------------------------------------
    window.addEventListener(
        "htmx:sendError",
        async (event) => {
            const info = inflight.get(event.target);
            if (!info) return;
            if (isLiveOnly(info.path)) {
                // Publish / Remind: no queueing, say why.
                event.stopImmediatePropagation();
                const text = msg("msgLiveOnly", "You're offline. This needs a connection, try again when you're back online.");
                setStatus(text);
                toast("error", text);
                return;
            }
            event.stopImmediatePropagation();
            const body = new URLSearchParams();
            info.pairs.forEach(([name, value]) => body.append(name, value));
            try {
                await putEntry({ user: USER, key: info.key, url: new URL(info.path, location.href).pathname + new URL(info.path, location.href).search, method: "POST", body: body.toString(), created: Date.now(), state: "pending", error: "" });
                keys.delete(info.sig); // the queue owns the key from here on
                await refreshStatus();
                registerSync();
                scheduleRetry();
            } catch (_) {
                // No IndexedDB (private window...): fall back to the plain offline error.
                const text = msg("msgNoQueue", "You're offline. Your change was not saved.");
                setStatus(text);
                toast("error", text);
            }
        },
        true,
    );

    // ---- replay ------------------------------------------------------------------------------------
    let flushing = false;
    let retryTimer = null;

    function csrfToken() {
        const fromBody = document.body && document.body.dataset && document.body.dataset.csrftoken;
        if (fromBody) return fromBody;
        const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    async function flush() {
        if (flushing || navigator.onLine === false) return;
        flushing = true;
        let synced = 0;
        try {
            let rows = await allEntries();
            // Purge anything that is not this user's (shared device, account switch).
            for (const row of rows.filter((r) => r.user !== USER)) await deleteEntry(row.seq);
            rows = rows.filter((r) => r.user === USER);
            for (const row of rows) {
                if (row.state === "failed") break; // keep order: wait for Discard/Retry
                let res;
                try {
                    res = await fetch(row.url, {
                        method: row.method,
                        credentials: "same-origin",
                        headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrfToken(), "HX-Request": "true", "Idempotency-Key": row.key },
                        body: row.body,
                    });
                } catch (_) {
                    break; // still offline: try again later
                }
                if (res.status === 409 || res.status >= 500) break; // in progress / server trouble: try again later
                const refused = res.redirected || res.status >= 400 || res.headers.get("X-Offline-Result") === "error";
                if (refused) {
                    row.state = "failed";
                    row.error = res.redirected || res.status === 401 || res.status === 403 ? msg("msgSession", "Your session has ended. Sign in again, then retry.") : msg("msgConflict", "A change saved offline was refused by the server (the event may have changed).");
                    await putEntry(row);
                    break;
                }
                await deleteEntry(row.seq);
                synced += 1;
            }
        } catch (_) {
            // IndexedDB unavailable: nothing to do.
        } finally {
            flushing = false;
        }
        if (synced) {
            toast("ok", fmt(msg("msgSynced", "Synced %(count)d offline changes"), synced));
            setStatus("");
            refreshBody();
        }
        await refreshStatus();
        scheduleRetry();
    }

    function refreshBody() {
        const target = document.getElementById("attendance-body") ? "#attendance-body" : document.getElementById("lineup-body") ? "#lineup-body" : null;
        if (!target || !window.htmx) return;
        window.htmx.ajax("GET", location.pathname + location.search, { target, select: target, swap: "outerHTML" });
    }

    function scheduleRetry() {
        clearTimeout(retryTimer);
        allEntries()
            .then((rows) => {
                if (rows.some((r) => r.user === USER && r.state !== "failed")) retryTimer = setTimeout(flush, 30000);
            })
            .catch(() => {});
    }

    function registerSync() {
        if (!("serviceWorker" in navigator)) return;
        navigator.serviceWorker.ready.then((reg) => (reg.sync ? reg.sync.register(SYNC_TAG) : null)).catch(() => {});
    }

    window.addEventListener("online", flush);
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") flush();
    });

    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.addEventListener("message", (event) => {
            const type = event.data && event.data.type;
            if (type === "flush-queue") flush();
            if (type === "signed-out") clearAll().catch(() => {});
        });
        // Tell the worker whose pages it may be holding (it drops its saved coach pages on a change of user).
        navigator.serviceWorker.ready.then((reg) => reg.active && reg.active.postMessage({ type: "coach-user", user: USER })).catch(() => {});
    }

    // Sign-out from this page: wipe the queue immediately, don't wait for the worker.
    document.addEventListener("submit", (event) => {
        const action = event.target && event.target.action;
        if (action && /\/logout\/?$/.test(pathOf(action))) clearAll().catch(() => {});
    });

    window.__rcOfflineQueue = { flush };
    refreshStatus().then(flush);
})();
