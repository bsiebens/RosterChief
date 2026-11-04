import { auth } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
	const r = await auth('GET', '/auth/email/verify', undefined, { fetch, headers: { 'x-email-verification-key': params.key } });
	return { valid: r.ok, email: r.data?.email as string | undefined };
};
