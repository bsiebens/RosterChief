import { api } from '#lib/management-api.ts';
import { loadOr } from '#lib/tournaments-server.ts';
import type { TeamDoc } from '#lib/tournaments.svelte.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const [res, clubTeams] = await Promise.all([
		loadOr<{ results: TeamDoc[]; payment_statuses: string[] }>(fetch, `/tournaments/${params.id}/teams`),
		api<{ id: string; name: string }[]>('GET', '/members/teams', { f: fetch })
	]);
	return {
		rows: res.data?.results ?? [],
		paymentStatuses: res.data?.payment_statuses?.length ? res.data.payment_statuses : ['unpaid', 'deposit_received', 'fully_paid'],
		clubTeams: clubTeams.data ?? [],
		error: res.data ? null : res.status
	};
};
