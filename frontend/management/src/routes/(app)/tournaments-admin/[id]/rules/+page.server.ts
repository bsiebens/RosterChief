import { loadOr } from '#lib/tournaments-server.ts';
import type { PageServerLoad } from './$types';

export type RulesDoc = { markdown: string; file_url: string | null; file_name: string };

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<RulesDoc>(fetch, `/tournaments/${params.id}/rules`);
	return { rules: data, error: data ? null : status };
};
