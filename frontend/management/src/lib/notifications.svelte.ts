/**
 * Notifications for the signed-in user (the bell in the top bar).
 *
 * The UI only talks to the store below; the store talks to a `NotificationSource`. The default source is the API
 * (`/api/v1/notifications`, through this app's same-origin proxy); `mockSource` is an in-memory list of dummy
 * notifications for trying the bell without a backend (`notifications.useSource(mockSource())`).
 */
export type NotificationKind = 'info' | 'success' | 'warning';

export type AppNotification = {
	id: string;
	kind: NotificationKind;
	title: string;
	body: string;
	/** ISO timestamp. */
	created: string;
	read: boolean;
	/** Where clicking it leads, when it points somewhere. */
	href?: string | null;
};

export interface NotificationSource {
	list(): Promise<AppNotification[]>;
	markRead(id: string): Promise<void>;
	markAllRead(): Promise<void>;
	clearAll(): Promise<void>;
}

/** The club API: always the signed-in user's own notifications in this club. */
export function apiSource(f: typeof fetch = fetch): NotificationSource {
	const call = async (method: string, path = '') => {
		const res = await f(`/api/v1/notifications${path}`, { method, headers: { accept: 'application/json' } });
		if (!res.ok) throw new Error(String(res.status));
		return res;
	};
	return {
		list: async () => (await (await call('GET')).json()).map((n: AppNotification) => ({ ...n, href: n.href ?? null })),
		markRead: async (id) => void (await call('POST', `/${encodeURIComponent(id)}/read`)),
		markAllRead: async () => void (await call('POST', '/read-all')),
		clearAll: async () => void (await call('DELETE'))
	};
}

const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

/** Dummy data for testing the bell before the API exists. Lives in memory: reload to get it back. */
export function mockSource(): NotificationSource {
	let items: AppNotification[] = [
		{ id: 'n1', kind: 'warning', title: 'Membership awaiting approval', body: 'Jane Doe signed up for the current season and is waiting to be activated.', created: minutesAgo(12), read: false, href: '/members?kind=member&status=pending' },
		{ id: 'n2', kind: 'success', title: 'Import finished', body: '24 members were imported from members.xlsx, 2 rows were skipped as duplicates.', created: minutesAgo(95), read: false, href: '/members' },
		{ id: 'n3', kind: 'warning', title: 'Team without coach', body: 'Sharks U14 has nobody with a management position this season.', created: minutesAgo(60 * 5), read: false, href: '/teams?coach=none' },
		{ id: 'n4', kind: 'info', title: 'Referee level expires soon', body: 'Sam Verhoeven’s referee level is valid until the end of this month.', created: minutesAgo(60 * 26), read: true, href: '/teams/officials' },
		{ id: 'n5', kind: 'info', title: 'New evaluation', body: 'Lena Maes added an evaluation for Alexander Siebens.', created: minutesAgo(60 * 24 * 3), read: true, href: null }
	];
	return {
		list: async () => items.map((i) => ({ ...i })),
		markRead: async (id) => void (items = items.map((i) => (i.id === id ? { ...i, read: true } : i))),
		markAllRead: async () => void (items = items.map((i) => ({ ...i, read: true }))),
		clearAll: async () => void (items = [])
	};
}

class NotificationStore {
	items = $state<AppNotification[]>([]);
	loaded = $state(false);
	#source: NotificationSource = apiSource();

	unread = $derived(this.items.filter((i) => !i.read).length);

	useSource(source: NotificationSource) {
		this.#source = source;
		this.loaded = false;
		return this.load();
	}

	async load() {
		try {
			this.items = await this.#source.list();
		} catch {
			// the bell just shows nothing when the source is down
		}
		this.loaded = true;
	}

	/** Optimistic: the list updates at once, the source follows (and a reload corrects it if the call failed). */
	async markRead(id: string) {
		this.items = this.items.map((i) => (i.id === id ? { ...i, read: true } : i));
		await this.#source.markRead(id).catch(() => this.load());
	}
	async markAllRead() {
		this.items = this.items.map((i) => ({ ...i, read: true }));
		await this.#source.markAllRead().catch(() => this.load());
	}
	async clearAll() {
		this.items = [];
		await this.#source.clearAll().catch(() => this.load());
	}
}

export const notifications = new NotificationStore();

/** Where a notification leads inside the member or coach app (the club's links point at the management app). */
export function notificationHref(href: string, portal?: 'parent' | 'coach'): string {
	if (!portal) return href;
	const event = href.match(/^\/calendar\/events\/([0-9a-f-]{36})(?:[/?#]|$)/i);
	if (event) return portal === 'coach' ? `/calendar/events/${event[1]}?view=coach` : `/parent?tab=calendar&event=${event[1]}`;
	const article = href.match(/^\/news\/([0-9a-f-]{36})(?:[/?#]|$)/i);
	if (article) return `/${portal}?tab=news&article=${article[1]}`;
	if (portal === 'parent') {
		if (/^\/forms(?:\/|$)/.test(href)) return '/parent/forms';
		if (/^\/shop(?:\/|$)/.test(href)) return '/parent/shop';
		if (/^\/(registrations|finance)(?:\/|$)/.test(href)) return '/parent?tab=payments';
	}
	return href;
}
