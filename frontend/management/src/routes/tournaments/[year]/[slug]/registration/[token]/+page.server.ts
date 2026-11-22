import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { RegistrationStatus } from '#lib/tournaments-public.ts';
import type { PageServerLoad } from './$types';

/** GET /public/tournaments/{slug}/registrations/{token}: possession of the token is the credential. A wrong token is a plain 404. */
export const load: PageServerLoad = async ({ params, fetch, setHeaders }) => {
	setHeaders({ 'cache-control': 'private, no-store', 'x-robots-tag': 'noindex, nofollow' });
	const res = await api<RegistrationStatus>('GET', `/public/tournaments/${encodeURIComponent(params.slug)}/registrations/${encodeURIComponent(params.token)}`, { f: fetch });
	if (res.status === 429) error(429, 'rate_limited');
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { status: res.data, token: params.token };
};
