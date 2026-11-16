const DEFAULT_ICON = '/images/app-icon-192.png';
const UUID = '[0-9a-f-]{36}';

self.addEventListener('install', (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

function destination(value) {
	if (typeof value !== 'string') return '/app';
	let target;
	try { target = new URL(value, self.location.origin); } catch { return '/app'; }
	if (target.origin !== self.location.origin || !/^https?:$/.test(target.protocol)) return '/app';

	const event = target.pathname.match(new RegExp('^/calendar/events/(' + UUID + ')/?$', 'i'));
	if (event) return '/app?event=' + event[1];
	const article = target.pathname.match(new RegExp('^/news/(' + UUID + ')/?$', 'i'));
	if (article) return '/app?article=' + article[1];
	if (/^\/forms(\/|$)/.test(target.pathname)) return '/parent?tab=forms';
	if (/^\/shop(\/|$)/.test(target.pathname)) return '/parent/shop';
	if (/^\/(registrations|finance)(\/|$)/.test(target.pathname)) return '/parent?tab=payments';
	return target.pathname === '/' ? '/app' : target.pathname + target.search + target.hash;
}

self.addEventListener('push', (event) => {
	let message = {};
	try { message = event.data?.json() || {}; } catch { message = { body: event.data?.text() }; }
	if (!message || typeof message !== 'object') message = {};
	const pointer = message.data && typeof message.data === 'object' ? message.data : {};
	const title = typeof message.title === 'string' && message.title.trim() ? message.title : 'RosterChief';
	const body = typeof message.body === 'string' ? message.body : '';
	event.waitUntil(self.registration.showNotification(title, {
		body,
		icon: DEFAULT_ICON,
		tag: typeof message.notification_id === 'string' ? message.notification_id : undefined,
		data: { href: destination(pointer.href || pointer.url || message.url) }
	}));
});

self.addEventListener('notificationclick', (event) => {
	event.notification.close();
	const href = destination(event.notification.data?.href);
	event.waitUntil((async () => {
		const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
		const url = new URL(href, self.location.origin).href;
		const exact = windows.find((client) => client.url === url);
		if (exact) { await exact.focus(); return; }
		const portal = windows.find((client) => new URL(client.url).pathname.startsWith('/parent'));
		if (portal) {
			try {
				const navigated = await portal.navigate(url);
				if (navigated) { await navigated.focus(); return; }
			} catch {
				// An app window may have closed between matchAll and navigate.
			}
		}
		await self.clients.openWindow(url);
	})());
});
