import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, MOVEMENT_REASONS, shopOn, type Movement, type PagedList, type StockRow } from '#lib/shop.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 30;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_inventory')) redirect(303, '/');
	const product = url.searchParams.get('product') ?? '';
	const reasonQ = url.searchParams.get('reason') ?? '';
	const reason = (MOVEMENT_REASONS as string[]).includes(reasonQ) ? reasonQ : '';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const [res, stock] = await Promise.all([
		api<PagedList<Movement> | Movement[]>('GET', '/shop/inventory/movements', { query: { product_id: product, reason, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch }),
		api<PagedList<StockRow> | StockRow[]>('GET', '/shop/inventory', { query: { limit: 200 }, f: fetch })
	]);
	const list = asList(res.data);
	const products = new Map<string, string>();
	for (const r of asList(stock.data).results) products.set(r.product_id, r.product_name);
	return {
		filters: { product, reason, offset },
		rows: list.results,
		count: list.count,
		pageSize: PAGE_SIZE,
		products: [...products].map(([id, name]) => ({ id, label: name })),
		error: res.ok ? null : res.status
	};
};
