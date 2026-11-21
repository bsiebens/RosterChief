import { loadOr } from '#lib/tournaments-server.ts';
import type { Person, TeamDoc } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export type RosterData = {
	team: TeamDoc;
	entries: Person[];
	lunch_mode: 'none' | 'optional' | 'mandatory';
	lunch_price_per_day: string | null;
	dates: string[];
	min_age: number | null;
	max_age: number | null;
	lunch: { date: string; player_count: number; staff_count: number; dietary_notes: string[] | string }[];
};

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<RosterData>(fetch, `/tournaments/${params.id}/teams/${params.team}/roster`);
	return { roster: data, error: data ? null : status, teamId: params.team };
};
