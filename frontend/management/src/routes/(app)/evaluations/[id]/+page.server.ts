import { error, fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { ChecklistDetail, ChecklistIn } from '#lib/evaluations.ts';
import type { FieldIn } from '#lib/forms.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_evaluations) redirect(303, '/evaluations');
	const item = await api<ChecklistDetail>('GET', `/evaluations/checklists/${params.id}`, { f: fetch });
	if (!item.ok) error(item.status >= 400 && item.status < 600 ? item.status : 500, item.message);
	return { item: item.data!, created: url.searchParams.get('created') === '1' };
};

const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);
const failed = (op: string, res: ApiCall<unknown>) => fail(httpStatus(res.status), { op, code: res.code ?? '', fields: res.fields, message: res.fields.__all__ || res.message || '', status: res.status });

export const actions: Actions = {
	/** The details (PATCH) and then the whole ordered list of criteria (PUT) in one go. */
	save: async ({ request, params, fetch }) => {
		let payload: { details: ChecklistIn; fields: FieldIn[] };
		try {
			payload = JSON.parse(String((await request.formData()).get('payload') ?? ''));
		} catch {
			return fail(400, { op: 'save', code: '', fields: {} as Record<string, string>, message: '', status: 400 });
		}
		const details = await api<ChecklistDetail>('PATCH', `/evaluations/checklists/${params.id}`, { body: payload.details, f: fetch });
		if (!details.ok) return failed('save', details);
		const criteria = await api<ChecklistDetail>('PUT', `/evaluations/checklists/${params.id}/criteria`, { body: { fields: payload.fields }, f: fetch });
		return criteria.ok ? { done: 'save' } : failed('save', criteria);
	},
	delete: async ({ params, fetch }) => {
		const res = await api('DELETE', `/evaluations/checklists/${params.id}`, { f: fetch });
		if (!res.ok) return failed('delete', res);
		redirect(303, '/evaluations?deleted=1');
	},
	/** The fallback when a checklist with evaluations cannot be deleted. */
	deactivate: async ({ params, fetch }) => {
		const res = await api<ChecklistDetail>('PATCH', `/evaluations/checklists/${params.id}`, { body: { is_active: false }, f: fetch });
		return res.ok ? { done: 'deactivate' } : failed('deactivate', res);
	}
};
