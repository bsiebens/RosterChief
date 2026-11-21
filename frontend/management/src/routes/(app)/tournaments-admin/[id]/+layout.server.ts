import { loadOr, requireTournaments } from '#lib/tournaments-server.ts';
import type { TournamentDetail } from '#lib/tournaments.svelte.ts';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ fetch, parent, params }) => {
	await requireTournaments(parent);
	const { data } = await loadOr<TournamentDetail>(fetch, `/tournaments/${params.id}`);
	return { tournament: data! };
};
