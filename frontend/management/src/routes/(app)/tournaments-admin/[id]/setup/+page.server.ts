import { loadOr } from '#lib/tournaments-server.ts';
import type { AttributeDoc, BreakRule, FieldDoc, StageDoc, TeamDoc } from '#lib/tournaments.svelte.ts';
import type { ScheduleData } from '../schedule/+page.server';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const [setup, schedule, teams, attributes] = await Promise.all([
		loadOr<{ fields: FieldDoc[]; stages: StageDoc[]; break_rules: BreakRule[]; dates: string[] }>(fetch, `/tournaments/${params.id}/setup`),
		loadOr<ScheduleData>(fetch, `/tournaments/${params.id}/schedule`),
		loadOr<{ results: TeamDoc[] }>(fetch, `/tournaments/${params.id}/teams`),
		loadOr<{ event_types: string[]; results: AttributeDoc[] }>(fetch, `/tournaments/${params.id}/attributes`)
	]);
	// The matches panel is secondary: when it fails to load the rest of the page still works.
	return { setup: setup.data, error: setup.data ? null : setup.status, schedule: schedule.data, teams: (teams.data?.results ?? []).filter((x) => !x.withdrawn_at), attributes: attributes.data };
};
