import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { badPayload, discountHref, emptyDiscount, failed, parsePayload, shopOn, type Discount, type DiscountIn } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_discounts')) redirect(303, '/');
	return { initial: emptyDiscount() };
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const body = parsePayload<DiscountIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<Discount>('POST', '/shop/discounts', { body: { ...body, code: body.code.trim().toUpperCase() }, f: fetch });
		if (!res.ok) return failed('save', res);
		redirect(303, `${discountHref(res.data!.id)}?created=1`);
	}
};
