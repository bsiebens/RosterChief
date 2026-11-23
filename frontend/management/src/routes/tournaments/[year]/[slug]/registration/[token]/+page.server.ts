import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { accessCookie } from '#lib/team-access.ts';
import type { RegistrationStatus } from '#lib/tournaments-public.ts';
import type { PageServerLoad } from './$types';

/**
 * GET /public/tournaments/{slug}/registrations/{token}: the private link plus the emailed password. Without a valid access token (the
 * cookie set after the password was accepted) the answer is 401 and the page shows the password form instead.
 */
export const load: PageServerLoad = async ({ params, fetch, cookies, setHeaders }) => {
	setHeaders({ 'cache-control': 'private, no-store', 'x-robots-tag': 'noindex, nofollow' });
	const access = cookies.get(accessCookie(params.token)) ?? '';
	const res = await api<RegistrationStatus>('GET', `/public/tournaments/${encodeURIComponent(params.slug)}/registrations/${encodeURIComponent(params.token)}`, {
		f: fetch,
		headers: access ? { 'x-team-access': access } : {}
	});
	if (res.status === 401) return { locked: true as const, token: params.token, status: null };
	if (res.status === 429) error(429, 'rate_limited');
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { locked: false as const, status: res.data, token: params.token };
};
