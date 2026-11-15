import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { ParentHome, ProfilePerson } from '#lib/portal.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [result, profile] = await Promise.all([api<ParentHome>('GET', '/portal/home', { f: fetch }), api<{ people: ProfilePerson[] }>('GET', '/portal/me/profile', { f: fetch })]);
	if (!result.ok || !result.data) error(result.status === 403 ? 403 : 502, result.message);
	// The family card shows teams and numbers from the profile; without it the card just leaves them out.
	return { home: result.data, profile: profile.ok ? (profile.data?.people ?? []) : [] };
};
