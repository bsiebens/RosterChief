import { api } from '#lib/management-api.ts';
import { requireTournaments } from '#lib/tournaments-server.ts';
import type { Season, TournamentItem } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent, url }) => {
	const me = await requireTournaments(parent);
	const season = url.searchParams.get('season') ?? '';
	const status = url.searchParams.get('status') ?? '';
	const [res, teams] = await Promise.all([
		api<{ results: TournamentItem[]; seasons: Season[]; can_manage: boolean }>('GET', '/tournaments', { f: fetch, query: { season, status } }),
		me.can_manage_tournaments ? api<{ id: string; name: string }[]>('GET', '/members/teams', { f: fetch }) : Promise.resolve(null)
	]);
	return { rows: res.data?.results ?? [], seasons: res.data?.seasons ?? [], season, status, teams: teams?.data ?? [], error: res.ok ? null : res.status };
};
