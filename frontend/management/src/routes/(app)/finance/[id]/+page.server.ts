import { error, fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { PaymentIn, RegistrationDetail } from '#lib/registrations.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_finance) redirect(303, '/');
	const res = await api<RegistrationDetail>('GET', `/registrations/${params.id}`, { f: fetch });
	if (!res.ok) error(res.status >= 400 && res.status < 600 ? res.status : 500, res.message);
	return { item: res.data! };
};

const failed = (op: string, res: ApiCall<unknown>) => fail(res.status >= 400 && res.status < 600 ? res.status : 500, { op, code: res.code ?? '', status: res.status, fields: res.fields, message: res.fields.__all__ || res.message || '' });

export const actions: Actions = {
	/** One payment against the invoice: `payload` is the JSON of a `PaymentIn`. */
	pay: async ({ request, params, fetch }) => {
		let body: PaymentIn;
		try {
			body = JSON.parse(String((await request.formData()).get('payload') ?? ''));
		} catch {
			return fail(400, { op: 'pay', code: '', status: 400, fields: {} as Record<string, string>, message: '' });
		}
		const res = await api<RegistrationDetail>('POST', `/registrations/${params.id}/payments`, { body, f: fetch });
		return res.ok ? { done: 'pay' } : failed('pay', res);
	},
	deletePayment: async ({ request, params, fetch }) => {
		const id = String((await request.formData()).get('payment_id') ?? '');
		const res = await api<RegistrationDetail>('DELETE', `/registrations/${params.id}/payments/${id}`, { f: fetch });
		return res.ok ? { done: 'deletePayment' } : failed('deletePayment', res);
	},
	/** The fixed instalment plan: `count` payments in all, `every` days apart (1 payment = pay in one go). */
	plan: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const count = Math.min(12, Math.max(1, Math.trunc(Number(f.get('count'))) || 1));
		const every = Math.trunc(Number(f.get('every'))) || 0;
		const offsets = Array.from({ length: count - 1 }, (_, i) => every * (i + 1));
		const res = await api<RegistrationDetail>('PUT', `/registrations/${params.id}/installments`, { body: { offsets }, f: fetch });
		return res.ok ? { done: 'plan' } : failed('plan', res);
	},
	resendInvoice: async ({ params, fetch }) => {
		const res = await api<{ sent: boolean }>('POST', `/registrations/${params.id}/resend-invoice`, { f: fetch });
		return res.ok ? { done: 'resendInvoice', sent: !!res.data?.sent } : failed('resendInvoice', res);
	}
};
