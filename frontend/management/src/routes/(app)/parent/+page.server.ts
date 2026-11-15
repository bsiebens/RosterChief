import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { ParentHome } from '#lib/portal.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const result = await api<ParentHome>('GET', '/portal/home', { f: fetch });
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	return { home: result.data };
};
