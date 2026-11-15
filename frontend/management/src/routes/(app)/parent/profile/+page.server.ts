import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PortalOverview, ProfilePerson, StatsPeople, TeamFollows } from '#lib/portal.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [result, overview, follows, stats] = await Promise.all([api<{ people: ProfilePerson[] }>('GET', '/portal/me/profile', { f: fetch }), api<PortalOverview>('GET', '/portal/me/overview', { f: fetch }), api<TeamFollows>('GET', '/portal/me/follows', { f: fetch }), api<StatsPeople>('GET', '/portal/me/stats', { f: fetch })]);
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	return { people: result.data.people, overview: overview.ok ? (overview.data ?? null) : null, follows: follows.ok ? (follows.data ?? null) : null, stats: stats.ok ? (stats.data ?? null) : null };
};
