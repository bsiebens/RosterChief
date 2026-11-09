import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, shopInvoicesOn, type InvoiceItem, type PagedList } from '#lib/shop-orders.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const STATUSES = ['all', 'unpaid', 'overdue', 'paid'] as const;
type Status = (typeof STATUSES)[number];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopInvoicesOn(me)) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const requested = url.searchParams.get('status') as Status;
	const status: Status = STATUSES.includes(requested) ? requested : 'all';
	const from = DATE.test(url.searchParams.get('from') ?? '') ? url.searchParams.get('from')! : '';
	const to = DATE.test(url.searchParams.get('to') ?? '') ? url.searchParams.get('to')! : '';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const res = await api<PagedList<InvoiceItem> | InvoiceItem[]>('GET', '/shop/invoices', {
		query: { q, status: status === 'all' ? undefined : status, issued_from: from, issued_to: to, limit: PAGE_SIZE, offset: offset || undefined },
		f: fetch
	});
	const list = asList(res.data);
	return { filters: { q, status, from, to, offset }, rows: list.results, count: list.count, pageSize: PAGE_SIZE, canOrders: me.can_manage_shop_orders, error: res.ok ? null : res.status };
};
