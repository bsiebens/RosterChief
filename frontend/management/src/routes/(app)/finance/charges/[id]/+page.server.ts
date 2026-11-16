import { error, redirect } from '@sveltejs/kit';
import { badPayload, chargesOn, failed, parsePayload, type ChargeDetail, type ChargeIn } from '#lib/charges.ts';
import { api } from '#lib/management-api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!chargesOn(me)) redirect(303, '/');
	const res = await api<ChargeDetail>('GET', `/finance/charges/${params.id}`, { f: fetch });
	if (!res.ok) error(res.status >= 400 && res.status < 600 ? res.status : 500, res.message);
	return { charge: res.data!, created: url.searchParams.get('created') === '1' };
};

const str = (f: FormData, k: string) => String(f.get(k) ?? '').trim();

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		const body = parsePayload<ChargeIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<ChargeDetail>('PUT', `/finance/charges/${params.id}`, { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	send: async ({ params, fetch }) => {
		const res = await api<ChargeDetail>('POST', `/finance/charges/${params.id}/send`, { body: {}, f: fetch });
		return res.ok ? { done: 'send' } : failed('send', res);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/finance/charges/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/finance/charges');
	},
	cancel: async ({ params, fetch }) => {
		const res = await api<ChargeDetail>('POST', `/finance/charges/${params.id}/cancel`, { f: fetch });
		return res.ok ? { done: 'cancel' } : failed('cancel', res);
	},
	remind: async ({ params, fetch }) => {
		const res = await api<ChargeDetail>('POST', `/finance/charges/${params.id}/remind`, { f: fetch });
		return res.ok ? { done: 'remind' } : failed('remind', res);
	},
	addPayment: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const body: Record<string, unknown> = { amount: str(f, 'amount'), method: str(f, 'method'), reference: str(f, 'reference'), note: str(f, 'note') };
		if (str(f, 'paid_at')) body.paid_at = new Date(str(f, 'paid_at')).toISOString();
		const res = await api('POST', `/finance/charges/${params.id}/payments`, { body, f: fetch });
		return res.ok ? { done: 'addPayment' } : failed('addPayment', res);
	},
	deletePayment: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const res = await api('DELETE', `/finance/charges/${params.id}/payments/${str(f, 'id')}`, { f: fetch });
		return res.ok ? { done: 'deletePayment' } : failed('deletePayment', res);
	}
};
