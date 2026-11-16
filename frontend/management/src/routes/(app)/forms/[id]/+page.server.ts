import { error, fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { FieldIn, FormDetail, FormIn } from '#lib/forms.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_forms) redirect(303, '/');
	const item = await api<FormDetail>('GET', `/forms/${params.id}`, { f: fetch });
	if (!item.ok) error(item.status >= 400 && item.status < 600 ? item.status : 500, item.message);
	return { item: item.data!, created: url.searchParams.get('created') === '1', duplicated: url.searchParams.get('duplicated') === '1' };
};

const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);
const failed = (op: string, res: ApiCall<unknown>) => fail(httpStatus(res.status), { op, code: res.code ?? '', fields: res.fields, message: res.fields.__all__ || res.message || '', status: res.status });

export const actions: Actions = {
	/** The details (PATCH) and then the whole ordered field list (PUT) in one go. */
	save: async ({ request, params, fetch }) => {
		let payload: { details: FormIn; fields: FieldIn[] };
		try {
			payload = JSON.parse(String((await request.formData()).get('payload') ?? ''));
		} catch {
			return fail(400, { op: 'save', code: '', fields: {} as Record<string, string>, message: '', status: 400 });
		}
		const details = await api<FormDetail>('PATCH', `/forms/${params.id}`, { body: payload.details, f: fetch });
		if (!details.ok) return failed('save', details);
		const fields = await api<FormDetail>('PUT', `/forms/${params.id}/fields`, { body: { fields: payload.fields }, f: fetch });
		return fields.ok ? { done: 'save' } : failed('save', fields);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/forms/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/forms?deleted=1');
	},
	deactivate: async ({ params, fetch }) => {
		const res = await api<FormDetail>('PATCH', `/forms/${params.id}`, { body: { is_active: false }, f: fetch });
		return res.ok ? { done: 'deactivate' } : failed('deactivate', res);
	}
};
