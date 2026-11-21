import { loadOr } from '#lib/tournaments-server.ts';
import type { BreakRule, FieldDoc, StageDoc } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<{ fields: FieldDoc[]; stages: StageDoc[]; break_rules: BreakRule[]; dates: string[] }>(fetch, `/tournaments/${params.id}/setup`);
	return { setup: data, error: data ? null : status };
};
