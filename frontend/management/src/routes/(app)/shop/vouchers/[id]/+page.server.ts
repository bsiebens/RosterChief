import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { badPayload, failed, parsePayload, shopOn, type Voucher, type VoucherDetail, type VoucherIn } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_discounts')) redirect(303, '/');
	const item = await api<VoucherDetail>('GET', `/shop/vouchers/${params.id}`, { f: fetch });
	if (!item.ok) error(item.status >= 400 && item.status < 600 ? item.status : 500, item.message);
	return { item: item.data!, created: url.searchParams.get('created') === '1' };
};

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		const body = parsePayload<VoucherIn>(await request.formData());
		if (!body) return badPayload('save');
		const { count: _count, ...rest } = body;
		void _count;
		const res = await api<Voucher>('PATCH', `/shop/vouchers/${params.id}`, { body: rest, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	toggle: async ({ request, params, fetch }) => {
		const activate = (await request.formData()).get('activate') === '1';
		const res = await api<Voucher>('POST', `/shop/vouchers/${params.id}/${activate ? 'activate' : 'deactivate'}`, { f: fetch });
		return res.ok ? { done: activate ? 'activate' : 'deactivate' } : failed('toggle', res);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/shop/vouchers/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/shop/vouchers?deleted=1');
	},
	/** Record an offline use of the voucher. */
	consume: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const res = await api('POST', `/shop/vouchers/${params.id}/consumptions`, { body: { amount: String(form.get('amount') ?? ''), note: String(form.get('note') ?? '') }, f: fetch });
		return res.ok ? { done: 'consume' } : failed('consume', res);
	},
	/** Remove a manual use again: the amount goes back to the voucher. */
	unconsume: async ({ request, params, fetch }) => {
		const id = String((await request.formData()).get('consumption_id') ?? '');
		const res = await api('DELETE', `/shop/vouchers/${params.id}/consumptions/${id}`, { f: fetch });
		return res.ok ? { done: 'unconsume' } : failed('unconsume', res);
	}
};
