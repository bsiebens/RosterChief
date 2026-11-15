import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { FollowedGame, ParentHome, ProfilePerson } from '#lib/portal.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [result, profile, followed] = await Promise.all([
		api<ParentHome>('GET', '/portal/home', { f: fetch }),
		api<{ people: ProfilePerson[] }>('GET', '/portal/me/profile', { f: fetch }),
		api<FollowedGame[]>('GET', '/portal/followed', { f: fetch })
	]);
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	// The family card shows teams and numbers from the profile, the top cards the followed games; without them those parts just stay out.
	return { home: result.data, profile: profile.ok ? (profile.data?.people ?? []) : [], followed: followed.ok ? (followed.data ?? []) : [] };
};
