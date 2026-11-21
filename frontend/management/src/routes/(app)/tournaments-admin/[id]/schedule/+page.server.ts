import { loadOr } from '#lib/tournaments-server.ts';
import type { MatchDoc, ScheduleBlock, StageDoc, TeamDoc } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export type Round = { round_number: number; label: string; all_complete: boolean; matches: MatchDoc[] };
export type ScheduleData = {
	tournament: { id: string; name: string; default_match_duration_minutes: number | null; schedule_needs_regeneration: boolean };
	stages: { stage: Pick<StageDoc, 'id' | 'name' | 'format'>; rounds: Round[] }[];
	fields: { id: string; name: string }[];
	blocks: ScheduleBlock[];
};
export type BoardSlot = { type: 'match' | 'gap'; start: string; end: string; minutes: number; height_px: number; top_px: number; match?: MatchDoc; rule?: { id: string; summary: string } };
export type BoardData = {
	px_per_minute: number;
	fields: { field: { id: string; name: string }; rules: { id: string; summary: string }[]; days: { date: string; start: string; end: string; height_px: number; hour_marks: unknown[]; slots: BoardSlot[] }[] }[];
};

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const [schedule, board, teams] = await Promise.all([
		loadOr<ScheduleData>(fetch, `/tournaments/${params.id}/schedule`),
		loadOr<BoardData>(fetch, `/tournaments/${params.id}/schedule/board`),
		loadOr<{ results: TeamDoc[] }>(fetch, `/tournaments/${params.id}/teams`)
	]);
	return { schedule: schedule.data, board: board.data, teams: (teams.data?.results ?? []).filter((x) => !x.withdrawn_at), error: schedule.data ? null : schedule.status };
};
