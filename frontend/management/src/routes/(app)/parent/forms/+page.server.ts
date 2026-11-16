import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { ParentHome, SharedEvaluation } from '#lib/portal.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [home, evaluations] = await Promise.all([api<ParentHome>('GET', '/portal/home', { f: fetch }), api<SharedEvaluation[]>('GET', '/portal/evaluations', { f: fetch })]);
	if (!home.ok || !home.data) error(home.status === 403 ? 403 : 502, home.message);
	// Evaluations only appear when the club has released one; if that call fails the section simply stays out.
	return { home: home.data, evaluations: evaluations.ok ? (evaluations.data ?? []) : [] };
};
