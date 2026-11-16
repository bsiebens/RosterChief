import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, badPayload, failed, parsePayload, shopOn, type Movement, type PagedList, type ReceiveIn, type StockRow } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_inventory')) redirect(303, '/');
	const res = await api<PagedList<StockRow> | StockRow[]>('GET', '/shop/inventory', { query: { limit: 200 }, f: fetch });
	return { rows: asList(res.data).results };
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const body = parsePayload<ReceiveIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<{ movements: Movement[] }>('POST', '/shop/inventory/receive', { body, f: fetch });
		if (!res.ok) return failed('save', res);
		redirect(303, `/shop/inventory?received=${res.data?.movements.length ?? body.items.length}`);
	}
};
