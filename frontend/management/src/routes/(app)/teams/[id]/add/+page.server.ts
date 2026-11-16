import { error, fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { loadSeasons, type Position, type TeamDetail } from '#lib/teams/server.ts';
import type { Actions, PageServerLoad } from './$types';

export type PersonResult = { index: number; ok: boolean; role: 'player' | 'staff' | null; error: string | null };

export const load: PageServerLoad = async ({ params, url, fetch }) => {
	const season = url.searchParams.get('season') ?? '';
	const [team, seasons, positions] = await Promise.all([
		api<TeamDetail>('GET', `/teams/${params.id}`, { query: { season }, f: fetch }),
		loadSeasons(fetch),
		api<Position[]>('GET', '/team-positions', { f: fetch })
	]);
	if (!team.ok) error(team.status === 404 ? 404 : team.status || 500);
	return { team: team.data!, season, seasons, positions: positions.data ?? [] };
};

export const actions: Actions = {
	add: async ({ request, params, url, fetch }) => {
		const form = await request.formData();
		let rows: unknown;
		try {
			rows = JSON.parse(String(form.get('rows') ?? '[]'));
		} catch {
			return fail(400, { error: 'Invalid data.' });
		}
		const season = url.searchParams.get('season') ?? '';
		const res = await api<{ added: number; rows: PersonResult[] }>('POST', `/teams/${params.id}/people`, { body: { season_id: season || null, rows }, f: fetch });
		if (!res.ok) return fail(res.status || 500, { error: res.fields.rows || res.message || 'failed' });
		// Everyone added: back to the team. Otherwise stay, with the reasons for the rows that did not go in.
		if (res.data!.rows.every((r) => r.ok)) redirect(303, `/teams/${params.id}${season ? `?season=${season}` : ''}`);
		return { result: res.data! };
	}
};
