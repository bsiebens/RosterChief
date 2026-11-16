import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { formHref, type FormDetail, type FormIn } from '#lib/forms.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent }) => {
	const { me } = await parent();
	if (!me.can_manage_forms) redirect(303, '/');
	return { initial: { title: '', description: '', login_required: false, is_active: true } satisfies FormIn };
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		let body: FormIn;
		try {
			body = JSON.parse(String(form.get('payload') ?? ''));
		} catch {
			return fail(400, { fields: {} as Record<string, string>, message: 'Invalid data.', status: 400 });
		}
		const res = await api<FormDetail>('POST', '/forms', { body, f: fetch });
		if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { fields: res.fields, message: res.message ?? '', status: res.status });
		redirect(303, `${formHref(res.data!.id)}?created=1`);
	}
};
