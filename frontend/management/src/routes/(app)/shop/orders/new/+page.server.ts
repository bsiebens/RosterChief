import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, type PagedList, type ProductDetail, type ProductItem } from '#lib/shop.ts';
import { badPayload, failed, orderHref, parsePayload, shopOrdersOn, type OrderDetail, type OrderIn, type ShopSettings } from '#lib/shop-orders.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!shopOrdersOn(me)) redirect(303, '/');
	// Active products, and the variants of the ones that have any (the list has no variants).
	const [res, settings] = await Promise.all([
		api<PagedList<ProductItem> | ProductItem[]>('GET', '/shop/products', { query: { active: true, limit: 200 }, f: fetch }),
		api<ShopSettings>('GET', '/shop/settings', { f: fetch })
	]);
	const items = asList(res.data).results;
	const products = await Promise.all(
		items.map(async (p): Promise<ProductDetail> => {
			if (!p.variant_count) return { ...p, variants: [], order_line_count: 0 };
			const d = await api<ProductDetail>('GET', `/shop/products/${p.id}`, { f: fetch });
			return d.ok ? d.data! : { ...p, variants: [], order_line_count: 0 };
		})
	);
	return { products, shopOpen: settings.ok ? settings.data!.shop_open : true, error: res.ok ? null : res.status };
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const body = parsePayload<OrderIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<OrderDetail>('POST', '/shop/orders', { body, f: fetch });
		if (!res.ok) return failed('save', res);
		redirect(303, `${orderHref(res.data!.id)}?created=1`);
	}
};
