import { error, fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { ReleasePreview } from '#lib/evaluations.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_execute_evaluations) redirect(303, '/evaluations');
	const preview = await api<ReleasePreview>('GET', `/evaluations/checklists/${params.id}/release/preview`, { f: fetch });
	if (!preview.ok) error(preview.status >= 400 && preview.status < 600 ? preview.status : 500, preview.message);
	const level = url.searchParams.get('level') === 'full' ? 'full' : 'notes';
	return { preview: preview.data!, level, checklistId: params.id };
};

export const actions: Actions = {
	default: async ({ request, params, fetch }) => {
		const items = JSON.parse(String((await request.formData()).get('items') ?? '[]'));
		const res = await api<{ count: number }>('PUT', `/evaluations/checklists/${params.id}/release`, { body: { items }, f: fetch });
		if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { message: res.fields.__all__ || res.message || '' });
		redirect(303, `/evaluations/${params.id}/meeting?shared=${res.data?.count ?? 0}`);
	}
};
