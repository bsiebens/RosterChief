import { error, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { ChecklistDetail, Matrix, Season, Stats } from '#lib/evaluations.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_execute_evaluations) redirect(303, '/evaluations');
	const [checklist, seasons] = await Promise.all([api<ChecklistDetail>('GET', `/evaluations/checklists/${params.id}`, { f: fetch }), api<Season[]>('GET', '/evaluations/seasons', { f: fetch })]);
	if (!checklist.ok) error(checklist.status >= 400 && checklist.status < 600 ? checklist.status : 500, checklist.message);
	const list = seasons.data ?? [];
	// The current season unless another one (or "all") is asked for.
	const asked = url.searchParams.get('season');
	const season = asked === 'all' ? 'all' : list.find((s) => s.id === asked)?.id ?? list.find((s) => s.is_current)?.id ?? 'all';
	const query = { season_id: season === 'all' ? undefined : season };
	const [stats, matrix] = await Promise.all([api<Stats>('GET', `/evaluations/checklists/${params.id}/stats`, { query, f: fetch }), api<Matrix>('GET', `/evaluations/checklists/${params.id}/matrix`, { query, f: fetch })]);
	return { checklist: checklist.data!, seasons: list, season, stats: stats.data ?? null, matrix: matrix.data ?? null, error: stats.ok && matrix.ok ? null : stats.status || matrix.status };
};
