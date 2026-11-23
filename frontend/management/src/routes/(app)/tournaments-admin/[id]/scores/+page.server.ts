import { loadOr } from '#lib/tournaments-server.ts';
import { api } from '#lib/management-api.ts';
import type { MatchDoc, ResultAttribute, StageDoc } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export type ScoresData = {
	stages: {
		stage: Pick<StageDoc, 'id' | 'name' | 'format' | 'allow_draws' | 'penalty_shots'>;
		ready_for_next_phase: boolean;
		swiss_ready_for_next_round: boolean;
		rounds: { round_number: number; label: string; all_complete: boolean; matches: MatchDoc[] }[];
	}[];
};

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<ScoresData>(fetch, `/tournaments/${params.id}/scores`);
	// The result attributes only add inputs; if they cannot be loaded the page still works without them.
	const attrs = await api<{ results: ResultAttribute[] }>('GET', `/tournaments/${params.id}/attributes`, { f: fetch });
	return { scores: data, error: data ? null : status, attributes: attrs.ok ? (attrs.data?.results ?? []) : [] };
};
