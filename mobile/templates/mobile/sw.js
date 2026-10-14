// RosterChief member app -- service worker. Served at /app/sw.js by
// mobile.views.ServiceWorkerView (not /static/) so its default scope is /app/.
//
// v1 scope: push notifications + a minimal offline-friendly cache for the app
// shell's own static assets. No page-content caching -- every screen here is
// live club data (RSVP status, dues, news), and serving a stale copy while
// offline would be actively misleading. The offline attendance queue
// described in the design doc is Coach mode, a later phase.

const SHELL_CACHE = "rosterchief-shell-v3";
const OFFLINE_URL = "/app/offline/";
const SHELL_ASSETS = ["/static/css/mobile.css", "/static/js/htmx.js", "/static/js/alpine.js", "/static/js/mobile-app.js"];

self.addEventListener("install", (event) => {
    // The offline page is precached so a failed navigation always has something to show; no other page is ever cached.
    event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll([...SHELL_ASSETS, OFFLINE_URL])));
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key)))),
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    if (event.request.method !== "GET") return;
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
