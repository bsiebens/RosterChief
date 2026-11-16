import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { badPayload, failed, parsePayload, shopOrdersOn, type PickupList } from '#lib/shop-orders.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!shopOrdersOn(me)) redirect(303, '/');
	const res = await api<PickupList>('GET', '/shop/pickup', { f: fetch });
	const d = res.ok ? res.data : undefined;
	return {
		orders: d?.orders ?? [],
		pickupInstructions: d?.pickup_instructions ?? '',
		paymentInstructions: d?.payment_instructions ?? '',
		error: res.ok ? null : res.status
	};
};

type Payload = { order_ids: string[]; pickup_instructions: string; payment_instructions: string; save_as_default: boolean };

export const actions: Actions = {
	confirm: async ({ request, fetch }) => {
		const body = parsePayload<Payload>(await request.formData());
		if (!body) return badPayload('confirm');
		const res = await api<{ count: number; skipped: number }>('POST', '/shop/pickup/confirm', { body, f: fetch });
		return res.ok ? { done: 'confirm', count: res.data?.count ?? 0, skipped: res.data?.skipped ?? 0 } : failed('confirm', res);
	}
};
