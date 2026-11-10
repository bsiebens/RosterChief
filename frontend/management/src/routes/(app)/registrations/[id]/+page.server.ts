import { error, fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { RegistrationDetail, ReviewIn } from '#lib/registrations.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_members) redirect(303, '/');
	const [res, teams, settings] = await Promise.all([api<RegistrationDetail>('GET', `/registrations/${params.id}`, { f: fetch }), api<{ id: string; name: string }[]>('GET', '/teams', { f: fetch }), api<{ invoice_due_days: number }>('GET', '/registration/settings', { f: fetch })]);
	if (!res.ok) error(res.status >= 400 && res.status < 600 ? res.status : 500, res.message);
	return { item: res.data!, origin: url.origin, dueDays: settings.data?.invoice_due_days ?? 30, teams: (teams.data ?? []).map((x) => ({ id: x.id, name: x.name })) };
};

const failed = (op: string, res: ApiCall<unknown>) => fail(res.status >= 400 && res.status < 600 ? res.status : 500, { op, code: res.code ?? '', status: res.status, fields: res.fields, message: res.fields.__all__ || res.message || '' });

export const actions: Actions = {
	/** The billing review: line prices / exclusions and the manual discount. */
	save: async ({ request, params, fetch }) => {
		let body: ReviewIn;
		try {
			body = JSON.parse(String((await request.formData()).get('payload') ?? ''));
		} catch {
			return fail(400, { op: 'save', code: '', status: 400, fields: {} as Record<string, string>, message: '' });
		}
		const res = await api<RegistrationDetail>('PATCH', `/registrations/${params.id}`, { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	confirm: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const due = String(f.get('due_date') ?? '').trim();
		const res = await api<RegistrationDetail>('POST', `/registrations/${params.id}/confirm`, { body: { due_date: due || null, send: f.get('send') === 'on' }, f: fetch });
		return res.ok ? { done: 'confirm', dropped: res.data?.numbers_dropped?.length ?? 0 } : failed('confirm', res);
	},
	cancel: async ({ params, fetch }) => {
		const res = await api<RegistrationDetail>('POST', `/registrations/${params.id}/cancel`, { f: fetch });
		return res.ok ? { done: 'cancel' } : failed('cancel', res);
	},
	resendConfirmation: async ({ params, fetch }) => {
		const res = await api<{ sent: boolean }>('POST', `/registrations/${params.id}/resend-confirmation`, { f: fetch });
		return res.ok ? { done: 'resendConfirmation', sent: !!res.data?.sent } : failed('resendConfirmation', res);
	},
	resendInvoice: async ({ params, fetch }) => {
		const res = await api<{ sent: boolean }>('POST', `/registrations/${params.id}/resend-invoice`, { f: fetch });
		return res.ok ? { done: 'resendInvoice', sent: !!res.data?.sent } : failed('resendInvoice', res);
	}
};
