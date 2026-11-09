import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { loadAllStock } from '#lib/shop-stock.server.ts';
import { badPayload, failed, parsePayload, shopOn, type CountResult } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_inventory')) redirect(303, '/');
	return await loadAllStock(fetch);
};

export const actions: Actions = {
	/** Apply the entered counts: `{ note, counts: [{ product_id, variant_id, counted, note }] }`. */
	apply: async ({ request, fetch }) => {
		const body = parsePayload<{ note: string; counts: { product_id: string; variant_id: string | null; counted: number; note: string }[] }>(await request.formData());
		if (!body) return badPayload('apply');
		const res = await api<CountResult>('POST', '/shop/inventory/count', { body, f: fetch });
		return res.ok && res.data ? { done: 'apply', result: res.data } : failed('apply', res);
	}
};
