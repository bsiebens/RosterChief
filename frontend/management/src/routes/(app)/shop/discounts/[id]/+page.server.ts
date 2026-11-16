import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { badPayload, failed, parsePayload, shopOn, type Discount, type DiscountIn } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_discounts')) redirect(303, '/');
	const item = await api<Discount>('GET', `/shop/discounts/${params.id}`, { f: fetch });
	if (!item.ok) error(item.status >= 400 && item.status < 600 ? item.status : 500, item.message);
	return { item: item.data!, created: url.searchParams.get('created') === '1' };
};

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		const body = parsePayload<DiscountIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<Discount>('PATCH', `/shop/discounts/${params.id}`, { body: { ...body, code: body.code.trim().toUpperCase() }, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/shop/discounts/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/shop/discounts?deleted=1');
	},
	deactivate: async ({ params, fetch }) => {
		const res = await api<Discount>('PATCH', `/shop/discounts/${params.id}`, { body: { is_active: false }, f: fetch });
		return res.ok ? { done: 'deactivate' } : failed('deactivate', res);
	}
};
