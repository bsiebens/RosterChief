import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { FollowedGame, ParentHome, PortalOverview, ProfilePerson } from '#lib/portal.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [result, profile, followed, overview] = await Promise.all([
		api<ParentHome>('GET', '/portal/home', { f: fetch }),
		api<{ people: ProfilePerson[] }>('GET', '/portal/me/profile', { f: fetch }),
		api<FollowedGame[]>('GET', '/portal/followed', { f: fetch }),
		api<PortalOverview>('GET', '/portal/me/overview', { f: fetch })
	]);
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	// The family card shows teams and numbers from the profile, the top cards the followed games; without them those parts just stay out.
	return { home: result.data, profile: profile.ok ? (profile.data?.people ?? []) : [], followed: followed.ok ? (followed.data ?? []) : [], overview: overview.ok ? (overview.data ?? null) : null };
};
