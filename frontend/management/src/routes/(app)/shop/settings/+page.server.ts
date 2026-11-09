import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { badPayload, failed, parsePayload, shopOrdersOn, type ShopSettings } from '#lib/shop-orders.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!shopOrdersOn(me)) redirect(303, '/');
	const res = await api<ShopSettings>('GET', '/shop/settings', { f: fetch });
	if (!res.ok) error(res.status >= 400 && res.status < 600 ? res.status : 500, res.message);
	return { settings: res.data! };
};

type Payload = { numbering?: Record<string, { pattern?: string; next?: number }>; shop_open: boolean; closed_message: string; payment_provider: string; invoice_due_days: number; pickup_instructions: string; credentials?: Record<string, string> };

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const body = parsePayload<Payload>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<ShopSettings>('PUT', '/shop/settings', { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	}
};
