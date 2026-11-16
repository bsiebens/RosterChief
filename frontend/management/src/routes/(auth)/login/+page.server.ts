import { auth } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const config = await auth('GET', '/config', undefined, { fetch });
	return { passkeyLogin: !!config.data?.mfa?.passkey_login_enabled };
};
