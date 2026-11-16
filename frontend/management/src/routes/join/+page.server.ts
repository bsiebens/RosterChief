import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PublicRegistration } from '#lib/registration-public.ts';
import type { PageServerLoad } from './$types';

/** GET /public/registration: no login. The platform host and unknown clubs are a plain 404. */
export const load: PageServerLoad = async ({ fetch }) => {
	const res = await api<PublicRegistration>('GET', '/public/registration', { f: fetch });
	if (!res.ok || !res.data) error(res.status === 404 ? 404 : 500);
	return { config: res.data };
};
