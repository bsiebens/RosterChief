import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { badPayload, failed, parsePayload } from '#lib/shop.ts';
import type { SponsorAgreement, SponsorAgreementIn, SponsorRow } from '#lib/sponsor-billing.ts';
import type { Actions, PageServerLoad } from './$types';

const endpoint = '/sponsor-management';

export const load: PageServerLoad = async ({ params, parent, fetch }) => {
	const { me } = await parent();
	if (!me.can_manage_sponsors) redirect(303, '/');
	const [sponsor, agreement] = await Promise.all([
		api<SponsorRow>('GET', `${endpoint}/${params.id}`, { f: fetch }),
		api<SponsorAgreement | null>('GET', `${endpoint}/${params.id}/agreement`, { f: fetch })
	]);
	if (!sponsor.ok) error(sponsor.status >= 400 && sponsor.status < 600 ? sponsor.status : 500, sponsor.message);
	return { sponsor: sponsor.data!, agreement: agreement.data ?? null, canSeeInvoices: !!(me.can_manage_charges || me.can_manage_finance) };
};

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		const body = parsePayload<SponsorAgreementIn>(await request.formData());
		if (!body) return badPayload('save');
		const res = await api<SponsorAgreement>('PUT', `${endpoint}/${params.id}/agreement`, { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	/** Send the installment's invoice (again when it is already out). */
	send: async ({ request, params, fetch }) => {
		const id = String((await request.formData()).get('installment_id') ?? '');
		const res = await api<SponsorAgreement>('POST', `${endpoint}/${params.id}/installments/${encodeURIComponent(id)}/invoice`, { f: fetch });
		return res.ok ? { done: 'send' } : failed('send', res);
	}
};
