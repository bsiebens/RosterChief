import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { failed, shopOrdersOn, type DeliveryList, type OrderDetail } from '#lib/shop-orders.ts';
import type { Actions, PageServerLoad } from './$types';

const TABS = ['all', 'unpaid', 'paid'] as const;
type Tab = (typeof TABS)[number];

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOrdersOn(me)) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const requested = url.searchParams.get('tab') as Tab;
	const tab: Tab = TABS.includes(requested) ? requested : 'all';
	const res = await api<DeliveryList>('GET', '/shop/delivery', { query: { q, payment: tab }, f: fetch });
	const d = res.ok ? res.data : undefined;
	return { filters: { q, tab }, orders: d?.orders ?? [], counts: d?.counts ?? { all: 0, unpaid: 0, paid: 0 }, error: res.ok ? null : res.status };
};

const str = (f: FormData, k: string) => String(f.get(k) ?? '').trim();

export const actions: Actions = {
	markPaid: async ({ request, fetch }) => {
		const f = await request.formData();
		const res = await api<OrderDetail>('POST', `/shop/orders/${str(f, 'id')}/mark-paid`, { body: { method: str(f, 'method') || 'cash', reference: str(f, 'reference') }, f: fetch });
		return res.ok ? { done: 'markPaid', number: str(f, 'number') } : failed('markPaid', res);
	},
	handOver: async ({ request, fetch }) => {
		const f = await request.formData();
		const body: Record<string, unknown> = {};
		if (str(f, 'method')) body.method = str(f, 'method');
		if (str(f, 'reference')) body.reference = str(f, 'reference');
		if (str(f, 'allow_unpaid') === 'true') body.allow_unpaid = true;
		const res = await api<{ order: OrderDetail }>('POST', `/shop/delivery/${str(f, 'id')}/hand-over`, { body, f: fetch });
		return res.ok ? { done: 'handOver', number: str(f, 'number') } : failed('handOver', res);
	}
};
