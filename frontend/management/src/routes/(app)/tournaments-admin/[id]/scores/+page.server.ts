import { loadOr } from '#lib/tournaments-server.ts';
import type { MatchDoc, StageDoc } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export type ScoresData = {
	stages: {
		stage: Pick<StageDoc, 'id' | 'name' | 'format'>;
		ready_for_next_phase: boolean;
		swiss_ready_for_next_round: boolean;
		rounds: { round_number: number; label: string; all_complete: boolean; matches: MatchDoc[] }[];
	}[];
};

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<ScoresData>(fetch, `/tournaments/${params.id}/scores`);
	return { scores: data, error: data ? null : status };
};
