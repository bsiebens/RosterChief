import { auth } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const r = await auth('GET', '/auth/password/reset', undefined, { fetch, headers: { 'x-password-reset-key': params.key } });
	return { valid: r.ok, email: r.data?.user?.email as string | undefined };
};
