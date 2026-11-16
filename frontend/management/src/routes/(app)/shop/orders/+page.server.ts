import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, badPayload, failed, parsePayload, shopOrdersOn, type OrderItem, type PagedList, type ShopStats } from '#lib/shop-orders.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const TABS = ['all', 'unpaid', 'partial', 'to_fulfil', 'ready_to_confirm', 'ready', 'completed', 'cancelled'] as const;
type Tab = (typeof TABS)[number];
/** The API filters one status at a time, so each tab maps to one filter. */
const FILTERS: Record<Tab, Record<string, string | boolean>> = {
	all: {},
	unpaid: { payment_status: 'pending', fulfillment_status: 'not_ready' },
	partial: { payment_status: 'partially_paid' },
	to_fulfil: { fulfillment_status: 'not_ready' },
	ready_to_confirm: { ready_to_confirm: true },
	ready: { fulfillment_status: 'ready_for_pickup' },
	completed: { closed: true },
	cancelled: { fulfillment_status: 'cancelled' }
};

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOrdersOn(me)) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const requested = url.searchParams.get('tab') as Tab;
	const tab: Tab = TABS.includes(requested) ? requested : 'all';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	// "Unpaid" means nothing paid yet: the payment filter alone (the fulfilment part is only a hint for the other tabs).
	const filtersOf = (key: Tab) => {
		const f = { ...FILTERS[key] };
		if (key === 'unpaid') delete f.fulfillment_status;
		return f;
	};
	// The number on each tab follows the search, so it matches what the tab shows; the active tab reuses the list's own count.
	const countOf = (key: Tab) => (key === tab ? Promise.resolve(null) : api<PagedList<OrderItem> | OrderItem[]>('GET', '/shop/orders', { query: { q, ...filtersOf(key), limit: 1 }, f: fetch }));
	const [res, stats, ...others] = await Promise.all([
		api<PagedList<OrderItem> | OrderItem[]>('GET', '/shop/orders', { query: { q, ...filtersOf(tab), limit: PAGE_SIZE, offset: offset || undefined }, f: fetch }),
		api<ShopStats>('GET', '/shop/stats', { f: fetch }),
		...TABS.map(countOf)
	]);
	const list = asList(res.data);
	const counts = Object.fromEntries(TABS.map((key, i) => [key, key === tab ? list.count : (others[i] ? asList(others[i]!.data).count : 0)])) as Record<Tab, number>;
	return { filters: { q, tab, offset }, rows: list.results, count: list.count, counts, pageSize: PAGE_SIZE, stats: stats.ok ? (stats.data ?? null) : null, error: res.ok ? null : res.status };
};

export const actions: Actions = {
	markPaid: async ({ request, fetch }) => {
		const body = parsePayload<{ order_ids: string[]; method: string }>(await request.formData());
		if (!body) return badPayload('markPaid');
		const res = await api<{ count: number }>('POST', '/shop/orders/bulk/mark-paid', { body, f: fetch });
		return res.ok ? { done: 'markPaid', count: res.data?.count ?? 0 } : failed('markPaid', res);
	}
};
