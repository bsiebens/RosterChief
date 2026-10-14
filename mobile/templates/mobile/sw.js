// RosterChief member app -- service worker. Served at /app/sw.js by
// mobile.views.ServiceWorkerView (not /static/) so its default scope is /app/.
//
// Scope: push notifications, a cache for the app shell's own static assets, and
// ONE narrow page cache for Coach mode (see "Coach pages" below). Every other
// screen here is live club data (RSVP status, dues, news), and serving a stale
// copy of it while offline would be actively misleading, so those stay
// network-only. The offline attendance/line-up queue itself lives in
// static/js/mobile-offline-queue.js (IndexedDB); this worker only deletes it on
// sign-out and pokes open pages to replay it (Background Sync).
{% load i18n %}{% trans "Offline: showing saved version" as offline_banner %}
const SHELL_CACHE = "rosterchief-shell-v5";
const COACH_CACHE = "rosterchief-coach-pages-v1";
const OFFLINE_BANNER = "{{ offline_banner|escapejs }}";
const OFFLINE_DB = "rosterchief-offline";
const COACH_CACHE_MAX = 12;
const COACH_USER_KEY = "/__coach_user__";
// Exact coach screens that may be saved: Today, Events, Squad, and an event's Attendance
// (the last only when the server says it holds no private coach note, see below).
const COACH_PAGES = [/^\/app\/coach\/$/, /^\/app\/coach\/schedule\/$/, /^\/app\/coach\/squad\/$/];
const COACH_ATTENDANCE = /^\/app\/coach\/attendance\/[0-9a-f-]+\/$/;
const OFFLINE_URL = "/app/offline/";
const SHELL_ASSETS = ["/static/css/mobile.css", "/static/js/htmx.js", "/static/js/alpine.js", "/static/js/mobile-app.js", "/static/js/mobile-sheet.js", "/static/js/mobile-offline-queue.js"];

self.addEventListener("install", (event) => {
    // The offline page is precached so a failed navigation always has something to show; no other page is ever cached.
    event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll([...SHELL_ASSETS, OFFLINE_URL])));
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== SHELL_CACHE && key !== COACH_CACHE).map((key) => caches.delete(key)))),
    );
    self.clients.claim();
});

// ---- Coach pages -------------------------------------------------------------------
// Read-only copies of the last viewed coach screens, network-first: the network always
// wins when it answers; the copy is served (with a visible banner) only when the fetch
// itself fails. Rules:
//  * authenticated coach routes only (exact paths above); never a member/personal page;
//  * an Attendance page is saved only if the server marks it X-Offline-Cache: 1, which it
//    does only when no player on it carries a private coach note -- pages with notes are
//    never stored;
//  * redirects (an ended session lands on login) and non-200s are never stored, and a
//    redirect clears the saved pages;
//  * everything is deleted at sign-out (the logout POST, below) or when a different user
//    is seen on the device, and the cache holds at most COACH_CACHE_MAX pages (oldest out).
function coachPath(url) {
    return COACH_PAGES.some((re) => re.test(url.pathname)) || COACH_ATTENDANCE.test(url.pathname);
}

async function trimCoachCache(cache) {
    const keys = await cache.keys();
    const pages = keys.filter((request) => new URL(request.url).pathname !== COACH_USER_KEY);
    for (const request of pages.slice(0, Math.max(0, pages.length - COACH_CACHE_MAX))) await cache.delete(request);
}

async function clearPrivateData() {
    await caches.delete(COACH_CACHE);
    // Queued offline saves (static/js/mobile-offline-queue.js) belong to the signed-out user too.
    await new Promise((resolve) => {
        try {
            const req = indexedDB.deleteDatabase(OFFLINE_DB);
            req.onsuccess = req.onerror = req.onblocked = () => resolve();
        } catch {
            resolve();
        }
    });
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) client.postMessage({ type: "signed-out" });
}

async function coachNetworkFirst(request, url) {
    try {
        const response = await fetch(request);
        if (response.redirected || response.status === 401 || response.status === 403) {
            clearCoachPages();
        } else if (response.status === 200 && response.type === "basic" && (!COACH_ATTENDANCE.test(url.pathname) || response.headers.get("X-Offline-Cache") === "1")) {
            const cache = await caches.open(COACH_CACHE);
            await cache.put(url.pathname + url.search, response.clone());
            await trimCoachCache(cache);
        } else if (COACH_ATTENDANCE.test(url.pathname)) {
            // Now holds a private note (or can't be saved): drop any older copy.
            const cache = await caches.open(COACH_CACHE);
            await cache.delete(url.pathname + url.search);
        }
        return response;
    } catch (error) {
        const cache = await caches.open(COACH_CACHE);
        const saved = await cache.match(url.pathname + url.search);
        if (!saved) throw error;
        const html = await saved.text();
        const banner = '<div role="status" style="position:sticky;top:0;z-index:60;padding:6px 12px;background:#92400e;color:#fff;font:600 13px system-ui,sans-serif;text-align:center">' + OFFLINE_BANNER + "</div>";
        const body = /<body[^>]*>/i.test(html) ? html.replace(/<body[^>]*>/i, (tag) => tag + banner) : banner + html;
        return new Response(body, { status: 200, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
    }
}

function clearCoachPages() {
    return caches.delete(COACH_CACHE);
}

self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    // Sign-out: forget everything private this worker or the page holds.
    if (event.request.method === "POST" && url.origin === self.location.origin && /\/logout\/?$/.test(url.pathname)) {
        event.waitUntil(clearPrivateData());
        return;
    }
    if (event.request.method !== "GET") return;
    const boosted = event.request.headers.get("HX-Boosted") === "true";
    const plainHtmx = event.request.headers.get("HX-Request") === "true" && !boosted;
    if (url.origin === self.location.origin && coachPath(url) && !plainHtmx && (event.request.mode === "navigate" || boosted)) {
        // Only real navigations are ever stored; a boosted fetch is answered from the copy when offline.
        if (event.request.mode === "navigate") {
            event.respondWith(coachNetworkFirst(event.request, url).catch(() => caches.match(OFFLINE_URL).then((cached) => cached || Response.error())));
        } else {
            event.respondWith(
                fetch(event.request).catch(async () => {
                    const cache = await caches.open(COACH_CACHE);
                    const saved = await cache.match(url.pathname + url.search);
                    if (!saved) return Response.error();
                    return saved;
                }),
            );
        }
        return;
    }
    if (event.request.mode === "navigate") {
        // Network-only for pages (live club data is never served stale); the precached offline page only when the network fails.
        event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE_URL).then((cached) => cached || Response.error())));
        return;
    }
    if (!SHELL_ASSETS.some((asset) => event.request.url.endsWith(asset))) return;
    // Network-first, not cache-first: this app ships CSS/JS fixes constantly during
    // active development, and a cache-first shell would leave an already-installed
    // browser stuck on stale assets indefinitely. Falling back to the cache only
    // when the network request itself fails still gives the offline-shell behaviour.
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                const responseCopy = response.clone();
                caches.open(SHELL_CACHE).then((cache) => cache.put(event.request, responseCopy));
                return response;
            })
            .catch(() => caches.match(event.request)),
    );
});

self.addEventListener("push", (event) => {
    let payload = { title: "RosterChief", body: "" };
    if (event.data) {
        try {
            payload = event.data.json();
        } catch {
            payload.body = event.data.text();
        }
    }
    const url = payload.url || "/app/";
    event.waitUntil(
        (async () => {
            await self.registration.showNotification(payload.title, {
                body: payload.body,
                data: { url, target: payload.target || null, notification_id: payload.notification_id || null },
            });
            if (typeof payload.unread === "number") {
                // App-icon badge, plus the open windows so their in-page badge updates without a reload.
                if (self.navigator && "setAppBadge" in self.navigator) {
                    try {
                        await (payload.unread > 0 ? self.navigator.setAppBadge(payload.unread) : self.navigator.clearAppBadge());
                    } catch {
                        // Badging is best-effort.
                    }
                }
                const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
                for (const client of windows) client.postMessage({ type: "unread", count: payload.unread });
            }
        })(),
    );
});

// Deep link for a notification: its target type wins, then the payload url, then the app home.
function notificationUrl(data) {
    const target = data && data.target;
    if (target && target.type) {
        if (target.type === "calendar") return "/app/calendar/";
        if (target.type === "news" && target.slug) return "/app/news/" + encodeURIComponent(target.slug) + "/";
        if (target.type === "event" && target.id) return "/app/events/" + encodeURIComponent(target.id) + "/";
        if (target.type === "form_send" && target.id) return "/app/forms/" + encodeURIComponent(target.id) + "/";
        if (target.type === "order" && target.id) return "/app/shop/orders/" + encodeURIComponent(target.id) + "/";
    }
    return (data && data.url) || "/app/";
}

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const url = new URL(notificationUrl(event.notification.data), self.location.origin);
    // Never navigate anywhere outside this origin's /app/.
    if (url.origin !== self.location.origin || !url.pathname.startsWith("/app/")) url.pathname = "/app/";
    const path = url.pathname + url.search + url.hash;
    event.waitUntil(
        self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (clients) => {
            for (const client of clients) {
                const open = new URL(client.url);
                if (open.origin === self.location.origin && open.pathname.startsWith("/app/") && "focus" in client) {
                    await client.focus();
                    client.postMessage({ type: "navigate", url: path });
                    return;
                }
            }
            return self.clients.openWindow(path);
        }),
    );
});

self.addEventListener("message", (event) => {
    const data = event.data || {};
    if (data.type === "clear-private") {
        event.waitUntil(clearPrivateData());
    } else if (data.type === "coach-user") {
        // A different account on this device must never be shown the previous one's saved pages.
        event.waitUntil(
            (async () => {
                const cache = await caches.open(COACH_CACHE);
                const previous = await cache.match(COACH_USER_KEY);
                const user = String(data.user || "");
                if (previous && (await previous.text()) !== user) {
                    await caches.delete(COACH_CACHE);
                    const fresh = await caches.open(COACH_CACHE);
                    await fresh.put(COACH_USER_KEY, new Response(user));
                } else if (!previous) {
                    await cache.put(COACH_USER_KEY, new Response(user));
                }
            })(),
        );
    }
});

// Background Sync: the queue is replayed by an open page (it holds the CSRF token and the session
// context); if none is open the sync is rejected so the browser retries later.
self.addEventListener("sync", (event) => {
    if (event.tag !== "rc-coach-queue") return;
    event.waitUntil(
        self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
            if (!windows.length) throw new Error("no open window");
            for (const client of windows) client.postMessage({ type: "flush-queue" });
        }),
    );
});
