import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, badPayload, failed, parsePayload, shopOn, type Movement, type PagedList, type StockRow } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_inventory')) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const low = url.searchParams.get('low') === '1';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const res = await api<PagedList<StockRow> | StockRow[]>('GET', '/shop/inventory', { query: { q, low: low || undefined, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch });
	const list = asList(res.data);
	return { filters: { q, low, offset }, rows: list.results, count: list.count, pageSize: PAGE_SIZE, received: url.searchParams.get('received') ?? '', error: res.ok ? null : res.status };
};

export const actions: Actions = {
	/** Set the stock of one product / variant to a counted number; the difference is recorded as an adjustment. */
	adjust: async ({ request, fetch }) => {
		const body = parsePayload<{ product_id: string; variant_id: string | null; counted: number | string; note: string }>(await request.formData());
		if (!body) return badPayload('adjust');
		const res = await api<{ movement: Movement | null }>('POST', '/shop/inventory/adjust', { body: { ...body, counted: Number(body.counted) }, f: fetch });
		return res.ok ? { done: 'adjust', unchanged: !res.data?.movement } : failed('adjust', res);
	}
};
