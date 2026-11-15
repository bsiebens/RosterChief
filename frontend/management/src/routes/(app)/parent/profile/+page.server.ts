import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PortalOverview, ProfilePerson, TeamFollows } from '#lib/portal.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [result, overview, follows] = await Promise.all([api<{ people: ProfilePerson[] }>('GET', '/portal/me/profile', { f: fetch }), api<PortalOverview>('GET', '/portal/me/overview', { f: fetch }), api<TeamFollows>('GET', '/portal/me/follows', { f: fetch })]);
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	return { people: result.data.people, overview: overview.ok ? (overview.data ?? null) : null, follows: follows.ok ? (follows.data ?? null) : null };
};
