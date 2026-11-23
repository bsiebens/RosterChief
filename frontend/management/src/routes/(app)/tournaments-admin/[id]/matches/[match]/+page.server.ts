import { loadOr } from '#lib/tournaments-server.ts';
import type { GameTimeline } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<GameTimeline>(fetch, `/tournaments/matches/${params.match}/events`);
	return { game: data, error: data ? null : status, matchId: params.match };
};
