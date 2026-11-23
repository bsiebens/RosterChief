import { fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { SponsorBilling } from '#lib/sponsor-billing.ts';
import type { Actions, PageServerLoad } from './$types';

type Sponsor = {
	id: string;
	name: string;
	url: string;
	logo_url: string | null;
	start_date: string;
	end_date: string | null;
	status: 'active' | 'upcoming' | 'ended' | 'suspended';
	billing: SponsorBilling;
};

const endpoint = '/sponsor-management';

export const load: PageServerLoad = async ({ parent, fetch }) => {
	const { me } = await parent();
	if (!me.can_manage_sponsors) redirect(303, '/');
	const result = await api<Sponsor[]>('GET', endpoint, { f: fetch });
	return { sponsors: result.data ?? [], loadFailed: !result.ok };
};

const failed = (op: 'save' | 'delete', res: ApiCall<unknown>) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { op, message: res.fields.__all__ || res.message || '', fields: res.fields });

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		form.delete('id');
		const logo = form.get('logo');
		if (!(logo instanceof File) || !logo.size) form.delete('logo');
		if (!form.get('end_date')) form.delete('end_date');
		if (form.has('remove_logo')) form.set('remove_logo', 'true');
		const res = await api('POST', id ? `${endpoint}/${encodeURIComponent(id)}` : endpoint, { form, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	delete: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = encodeURIComponent(String(form.get('id') ?? ''));
		const res = await api('DELETE', `${endpoint}/${id}`, { f: fetch });
		return res.ok ? { done: 'delete' } : failed('delete', res);
	}
};
