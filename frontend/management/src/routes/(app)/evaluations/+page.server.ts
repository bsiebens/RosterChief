import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { ChecklistItem } from '#lib/evaluations.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_execute_evaluations && !me.can_manage_evaluations) redirect(303, '/');
	const res = await api<{ count: number; results: ChecklistItem[] }>('GET', '/evaluations/checklists', { f: fetch });
	return { rows: res.data?.results ?? [], error: res.ok ? null : res.status };
};
