import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { RegistrationSettings } from '#lib/registrations.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_members) redirect(303, '/');
	// The public endpoint says whether anything can be offered at all (membership products with a season).
	const [settings, offer] = await Promise.all([api<RegistrationSettings>('GET', '/registration/settings', { f: fetch }), api<{ products: unknown[] }>('GET', '/public/registration', { f: fetch })]);
	return { settings: settings.data ?? null, offerable: offer.ok ? (offer.data?.products.length ?? 0) : null, joinUrl: `${url.origin}/join`, error: settings.ok ? null : settings.status };
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const f = await request.formData();
		const body = {
			is_open: f.get('is_open') === 'on',
			intro_text: String(f.get('intro_text') ?? ''),
			terms_text: String(f.get('terms_text') ?? ''),
			payment_instructions: String(f.get('payment_instructions') ?? ''),
			invoice_due_days: Number(f.get('invoice_due_days') || 0)
		};
		const res = await api<RegistrationSettings>('PUT', '/registration/settings', { body, f: fetch });
		if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { fields: res.fields, message: res.fields.__all__ || res.message || '', values: body });
		return { done: 'save' };
	}
};
