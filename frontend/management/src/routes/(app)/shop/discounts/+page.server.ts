import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, badPayload, discountHref, DISCOUNT_STATUSES, failed, parsePayload, shopOn, type Discount, type DiscountIn, type PagedList } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_discounts')) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const s = url.searchParams.get('status') ?? '';
	const status = (DISCOUNT_STATUSES as string[]).includes(s) ? s : '';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const res = await api<PagedList<Discount> | Discount[]>('GET', '/shop/discounts', { query: { q, status, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch });
	const list = asList(res.data);
	return { filters: { q, status, offset }, rows: list.results, count: list.count, pageSize: PAGE_SIZE, error: res.ok ? null : res.status };
};

export const actions: Actions = {
	create: async ({ request, fetch }) => {
		const body = parsePayload<DiscountIn>(await request.formData());
		if (!body) return badPayload('create');
		const res = await api<Discount>('POST', '/shop/discounts', { body: { ...body, code: body.code.trim().toUpperCase() }, f: fetch });
		if (!res.ok) return failed('create', res);
		redirect(303, `${discountHref(res.data!.id)}?created=1`);
	},
	delete: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api('DELETE', `/shop/discounts/${id}`, { f: fetch });
		return res.ok ? { done: 'delete', id } : failed('delete', res, { id });
	},
	deactivate: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api<Discount>('PATCH', `/shop/discounts/${id}`, { body: { is_active: false }, f: fetch });
		return res.ok ? { done: 'deactivate', id } : failed('deactivate', res, { id });
	}
};
