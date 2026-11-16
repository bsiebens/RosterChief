import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { OrderHistory, ShopCatalog } from '#lib/portal-shop.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [catalog, orders] = await Promise.all([
		api<ShopCatalog>('GET', '/portal/shop', { f: fetch }),
		api<OrderHistory>('GET', '/portal/shop/orders', { f: fetch })
	]);
	if (!catalog.ok || !catalog.data) error(catalog.status === 403 || catalog.status === 404 ? catalog.status : 502, catalog.message);
	return { catalog: catalog.data, orders: orders.data ?? null };
};
