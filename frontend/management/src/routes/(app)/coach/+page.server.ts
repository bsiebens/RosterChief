import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { CoachHome } from '#lib/portal.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const result = await api<CoachHome>('GET', '/portal/coach', { f: fetch });
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	return { coach: result.data };
};
