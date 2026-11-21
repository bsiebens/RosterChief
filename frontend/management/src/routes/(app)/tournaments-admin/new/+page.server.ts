import { api } from '#lib/management-api.ts';
import { requireManage } from '#lib/tournaments-server.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	await requireManage(parent);
	const teams = await api<{ id: string; name: string }[]>('GET', '/members/teams', { f: fetch });
	return { teams: teams.data ?? [] };
};
