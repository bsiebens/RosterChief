import { error, fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { ChecklistDetail, EvaluationOut, Season } from '#lib/evaluations.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_execute_evaluations) redirect(303, '/evaluations');
	const playerId = url.searchParams.get('player') ?? '';
	const [checklist, seasons, player] = await Promise.all([
		api<ChecklistDetail>('GET', `/evaluations/checklists/${params.id}`, { f: fetch }),
		api<Season[]>('GET', '/evaluations/seasons', { f: fetch }),
		playerId ? api<{ id: string; first_name: string; last_name: string }>('GET', `/members/${playerId}`, { f: fetch }) : Promise.resolve(null)
	]);
	if (!checklist.ok) error(checklist.status >= 400 && checklist.status < 600 ? checklist.status : 500, checklist.message);
	return {
		checklist: checklist.data!,
		seasons: seasons.data ?? [],
		player: player?.ok && player.data ? { id: player.data.id, name: `${player.data.first_name} ${player.data.last_name}`.trim() } : null,
		saved: url.searchParams.get('saved') ?? ''
	};
};

export const actions: Actions = {
	default: async ({ request, params, fetch, url }) => {
		const f = await request.formData();
		const playerId = String(f.get('player_id') ?? '');
		const answers: Record<string, unknown> = {};
		for (const key of new Set([...f.keys()].filter((k) => k.startsWith('a.')))) {
			const values = f.getAll(key).map(String).filter((x) => x !== '');
			if (values.length) answers[key.slice(2)] = values.length > 1 ? values : values[0];
		}
		const body = { player_id: playerId, season_id: String(f.get('season_id') ?? '') || null, answers };
		const res = await api<EvaluationOut>('POST', `/evaluations/checklists/${params.id}/evaluations`, { body, f: fetch });
		if (!res.ok) return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { fields: res.fields, message: res.fields.__all__ || res.message || '', playerId });
		// Came from a member's page: back to it. From the checklist: stay, ready for the next player.
		if (url.searchParams.get('player')) redirect(303, `/members/${playerId}?tab=evaluations&evaluated=1`);
		return { saved: res.data!.player.name };
	}
};
