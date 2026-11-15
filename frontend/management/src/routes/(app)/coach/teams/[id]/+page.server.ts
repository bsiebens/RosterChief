import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { TeamDetail } from '#lib/teams/server.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const result = await api<TeamDetail>('GET', `/teams/${params.id}`, { f: fetch });
	if (!result.ok || !result.data) error(result.status || 502, result.message);
	return { team: result.data };
};
