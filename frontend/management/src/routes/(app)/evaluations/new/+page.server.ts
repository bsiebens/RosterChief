import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { checklistHref, type ChecklistDetail } from '#lib/evaluations.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent }) => {
	const { me } = await parent();
	if (!me.can_manage_evaluations) redirect(303, '/evaluations');
	return {};
};

export const actions: Actions = {
	default: async ({ request, fetch }) => {
		const f = await request.formData();
		const values = { name: String(f.get('name') ?? '').trim(), description: String(f.get('description') ?? '').trim() };
		const res = await api<ChecklistDetail>('POST', '/evaluations/checklists', { body: values, f: fetch });
		if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { values, fields: res.fields, message: res.fields.__all__ || res.message || '' });
		redirect(303, `${checklistHref(res.data!.id)}?created=1`);
	}
};
