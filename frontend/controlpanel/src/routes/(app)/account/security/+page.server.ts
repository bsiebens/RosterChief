import { auth } from '#lib/api.ts';
import type { Authenticator } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, depends, url }) => {
	depends('app:authenticators');
	const [list, config] = await Promise.all([auth<Authenticator[]>('GET', '/account/authenticators', undefined, { fetch }), auth('GET', '/config', undefined, { fetch })]);
	const types: string[] = config.data?.mfa?.supported_types ?? ['totp', 'recovery_codes', 'webauthn'];
	return { authenticators: list.data ?? [], types, required: url.searchParams.has('required') };
};
