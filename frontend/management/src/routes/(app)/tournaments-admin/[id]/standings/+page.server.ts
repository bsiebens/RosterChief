import { loadOr } from '#lib/tournaments-server.ts';
import type { MatchDoc, StageDoc, TTeamRef } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export type StandingRow = { rank: number; team: TTeamRef; played: number; won: number; drawn: number; lost: number; points: number; goals_for: number; goals_against: number; goal_diff: number };
export type StandingsData = {
	standings: { stage: Pick<StageDoc, 'id' | 'name' | 'format'>; tables: { group: { id: string; name: string } | null; rows: StandingRow[] }[] }[];
	brackets: { stage: Pick<StageDoc, 'id' | 'name' | 'format'>; rounds: { round_number?: number; label?: string; matches?: MatchDoc[] }[] }[];
};

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<StandingsData>(fetch, `/tournaments/${params.id}/standings`);
	return { standings: data, error: data ? null : status };
};
