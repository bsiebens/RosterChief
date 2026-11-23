import { loadOr, requireManage } from '#lib/tournaments-server.ts';
import type { Registration } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await requireManage(parent);
	const { data, status } = await loadOr<{ results: Registration[] }>(fetch, `/tournaments/${params.id}/registrations`);
	return { rows: data?.results ?? [], error: data ? null : status };
};
