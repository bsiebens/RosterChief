import { error, fail, redirect } from '@sveltejs/kit';
import type { EventOptions } from '#lib/events/types.ts';
import { api } from '#lib/management-api.ts';
import { sendHref, type FormDetail, type SendDetail, type SendIn } from '#lib/forms.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_forms) redirect(303, '/');
	const [item, options] = await Promise.all([api<FormDetail>('GET', `/forms/${params.id}`, { f: fetch }), api<EventOptions>('GET', '/events/options', { f: fetch })]);
	if (!item.ok) error(item.status >= 400 && item.status < 600 ? item.status : 500, item.message);
	if (!options.ok || !options.data) error(options.status >= 400 && options.status < 600 ? options.status : 500, options.message);
	return { form: item.data!, options: options.data };
};

export const actions: Actions = {
	save: async ({ request, params, fetch }) => {
		let body: SendIn;
		try {
			body = JSON.parse(String((await request.formData()).get('payload') ?? ''));
		} catch {
			return fail(400, { fields: {} as Record<string, string>, message: 'Invalid data.', status: 400 });
		}
		const res = await api<SendDetail>('POST', `/forms/${params.id}/sends`, { body, f: fetch });
		if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { fields: res.fields, message: res.fields.__all__ || res.message || '', status: res.status });
		redirect(303, `${sendHref(res.data!.id)}?created=1`);
	}
};
