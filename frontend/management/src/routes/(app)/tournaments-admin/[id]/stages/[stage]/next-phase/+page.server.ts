import { api } from '#lib/management-api.ts';
import type { TTeamRef } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export type NextPhase = {
	stage: { id: string; name: string };
	groups: {
		group: { id: string; name: string } | null;
		standings: {
			rank: number;
			team: TTeamRef;
			played?: number;
			points?: number;
			goal_diff?: number;
			feeds: { match_id: string; side: string; home_display: string; away_display: string; field: { id: string; name: string } | null; scheduled_start: string | null }[];
		}[];
	}[];
};

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const res = await api<NextPhase>('GET', `/tournaments/${params.id}/stages/${params.stage}/next-phase`, { f: fetch });
	return { phase: res.data ?? null, error: res.ok ? null : { status: res.status, code: res.code, message: res.message }, stageId: params.stage };
};
