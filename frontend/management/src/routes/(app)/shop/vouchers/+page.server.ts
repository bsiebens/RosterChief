import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, badPayload, failed, parsePayload, shopOn, voucherHref, VOUCHER_STATUSES, type PagedList, type Voucher, type VoucherIn } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_discounts')) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	// The usable vouchers are what people look for: that tab is the default; `status=all` shows every voucher.
	const s = url.searchParams.get('status') ?? 'usable';
	const tab = s === 'all' || (VOUCHER_STATUSES as string[]).includes(s) ? s : 'usable';
	const status = tab === 'all' ? '' : tab;
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const res = await api<PagedList<Voucher> | Voucher[]>('GET', '/shop/vouchers', { query: { q, status, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch });
	const list = asList(res.data);
	return { filters: { q, status: tab, offset }, rows: list.results, count: list.count, pageSize: PAGE_SIZE, created: url.searchParams.get('created') ?? '', error: res.ok ? null : res.status };
};

export const actions: Actions = {
	create: async ({ request, fetch }) => {
		const body = parsePayload<VoucherIn>(await request.formData());
		if (!body) return badPayload('create');
		const res = await api<{ count: number; results: Voucher[] }>('POST', '/shop/vouchers', { body: { ...body, count: body.count || 1 }, f: fetch });
		if (!res.ok) return failed('create', res);
		const list = res.data?.results ?? [];
		if (list.length === 1) redirect(303, `${voucherHref(list[0].id)}?created=1`);
		redirect(303, `/shop/vouchers?created=${res.data?.count ?? list.length}`);
	},
	toggle: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const activate = form.get('activate') === '1';
		const res = await api<Voucher>('POST', `/shop/vouchers/${id}/${activate ? 'activate' : 'deactivate'}`, { f: fetch });
		return res.ok ? { done: activate ? 'activate' : 'deactivate', id } : failed('toggle', res, { id });
	},
	delete: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api('DELETE', `/shop/vouchers/${id}`, { f: fetch });
		return res.ok ? { done: 'delete', id } : failed('delete', res, { id });
	}
};
