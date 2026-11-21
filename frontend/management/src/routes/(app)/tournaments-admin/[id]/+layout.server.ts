import { error } from '@sveltejs/kit';
import { loadOr, requireTournaments } from '#lib/tournaments-server.ts';
import type { TournamentDetail } from '#lib/tournaments.svelte.ts';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ fetch, parent, params }) => {
	await requireTournaments(parent);
	const { data, status } = await loadOr<TournamentDetail>(fetch, `/tournaments/${params.id}`);
	// Every tournament page reads this: a failed call is an error page, never an empty tournament.
	if (!data) error(status >= 400 ? status : 502, 'The tournament could not be loaded.');
	return { tournament: data };
};
