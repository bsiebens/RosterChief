import { redirect } from '@sveltejs/kit';
import { SESSION_COOKIE } from '#lib/server/proxy.ts';
import { issueHandoff } from '#lib/server/handoff.ts';
import type { RequestHandler } from './$types';

/** Bare domain only: take the signed-in user to one of their clubs, signed in there too (see handoff.ts). */
export const GET: RequestHandler = ({ locals, params, cookies }) => {
	const token = cookies.get(SESSION_COOKIE);
	if (locals.mode !== 'platform' || locals.auth.status !== 'authenticated' || !token) redirect(303, '/login');
	const club = locals.myClubs.find((c) => c.slug === params.slug); // only clubs the user belongs to
	if (!club) redirect(303, '/');
	const code = issueHandoff(token, new URL(club.url).hostname);
	// A different origin (the club's host): SvelteKit 3 only follows it when that exact origin is allowed.
	redirect(303, `${club.url}handoff?code=${encodeURIComponent(code)}`, { external: [new URL(club.url).origin] });
};
