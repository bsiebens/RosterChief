import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, badPayload, failed, parsePayload, shopOn, type Category, type PagedList, type ProductDetail, type ProductIn } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_products')) redirect(303, '/');
	const [item, cats] = await Promise.all([api<ProductDetail>('GET', `/shop/products/${params.id}`, { f: fetch }), api<PagedList<Category> | Category[]>('GET', '/shop/categories', { f: fetch })]);
	if (!item.ok) error(item.status >= 400 && item.status < 600 ? item.status : 500, item.message);
	return { item: item.data!, categories: asList(cats.data).results, created: url.searchParams.get('created') === '1' };
};

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		const body = parsePayload<ProductIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<ProductDetail>('PATCH', `/shop/products/${params.id}`, { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/shop/products/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/shop/products?deleted=1');
	},
	deactivate: async ({ params, fetch }) => {
		const res = await api<ProductDetail>('PATCH', `/shop/products/${params.id}`, { body: { is_active: false }, f: fetch });
		return res.ok ? { done: 'deactivate' } : failed('deactivate', res);
	}
};
