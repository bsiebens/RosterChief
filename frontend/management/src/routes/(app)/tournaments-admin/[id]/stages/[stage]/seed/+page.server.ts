import { loadOr } from '#lib/tournaments-server.ts';
import type { TTeamRef } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export type SeedData = {
	already_generated: boolean;
	slot_count: number;
	slots: { type: 'team'; team_id: string }[];
	teams: TTeamRef[];
	groups: { id: string; name: string; stage_id: string }[];
};

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<SeedData>(fetch, `/tournaments/${params.id}/stages/${params.stage}/seed`);
	return { seed: data, error: data ? null : status, stageId: params.stage };
};
