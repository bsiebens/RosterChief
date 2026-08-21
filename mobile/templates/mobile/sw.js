// RosterChief member app -- service worker. Served at /app/sw.js by
// mobile.views.ServiceWorkerView (not /static/) so its default scope is /app/.
//
// v1 scope: push notifications + a minimal offline-friendly cache for the app
// shell's own static assets. No page-content caching -- every screen here is
// live club data (RSVP status, dues, news), and serving a stale copy while
// offline would be actively misleading. The offline attendance queue
// described in the design doc is Coach mode, a later phase.

const SHELL_CACHE = "rosterchief-shell-v2";
const SHELL_ASSETS = ["/static/css/mobile.css", "/static/js/htmx.js", "/static/js/alpine.js", "/static/js/mobile-app.js"];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)));
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key)))),
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    if (event.request.method !== "GET" || !SHELL_ASSETS.some((asset) => event.request.url.endsWith(asset))) return;
    // Network-first, not cache-first: this app ships CSS/JS fixes constantly during
    // active development, and a cache-first shell would leave an already-installed
    // browser stuck on stale assets indefinitely (nothing ever re-triggers a fetch
    // once something is cached). Falling back to the cache only when the network
    // request itself fails still gives the offline-shell behaviour this exists for.
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
    event.waitUntil(
        self.registration.showNotification(payload.title, {
            body: payload.body,
            data: { url: payload.url || "/app/" },
        }),
    );
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const url = event.notification.data && event.notification.data.url ? event.notification.data.url : "/app/";
    event.waitUntil(
        self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
            for (const client of clients) {
                if (client.url === url && "focus" in client) return client.focus();
            }
            return self.clients.openWindow(url);
        }),
    );
});
