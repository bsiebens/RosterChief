import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { badPayload, failed, parsePayload, shopOn, voucherHref, type Voucher, type VoucherIn } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_discounts')) redirect(303, '/');
	// Default expiry: one year from today.
	const d = new Date();
	d.setFullYear(d.getFullYear() + 1);
	return { initial: { amount: '', expiry_date: d.toISOString().slice(0, 10), issued_to_id: null, notes: '', count: 1 } satisfies VoucherIn };
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const body = parsePayload<VoucherIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<{ count: number; results: Voucher[] }>('POST', '/shop/vouchers', { body: { ...body, count: body.count || 1 }, f: fetch });
		if (!res.ok) return failed('save', res);
		const list = res.data?.results ?? [];
		if (list.length === 1) redirect(303, `${voucherHref(list[0].id)}?created=1`);
		redirect(303, `/shop/vouchers?created=${res.data?.count ?? list.length}`);
	}
};
