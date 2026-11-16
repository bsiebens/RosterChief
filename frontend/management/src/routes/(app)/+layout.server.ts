import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { Me } from '#lib/types.ts';
import type { LayoutServerLoad } from './$types';

const SECURITY = '/account/security';
const OPEN_WHILE_ENROLLING = [SECURITY, '/account/password'];
/** On the bare domain (club picker) only the picker and the account screens exist. */
const PLATFORM_PATHS = ['/', '/account/password', '/account/security'];

/**
 * (app) contract, on top of the root's `auth`, `lang`, `mode`, `branding`:
 *   club mode:     `me` (the /me payload)
 *   platform mode: `clubs` lists the user's clubs (with absolute `url`s)
 * `me` is typed non-null for the feature pages. It is only really null on the picker, /no-access and the account
 * screens while MFA enrolment is pending; the guards above keep every other page away from that state.
 */
export const load: LayoutServerLoad = async ({ locals, url, fetch }) => {
	const a = locals.auth;
	if (a.status === 'anonymous') redirect(303, `/login${url.pathname !== '/' ? `?next=${encodeURIComponent(url.pathname + url.search)}` : ''}`);
	if (a.status === 'mfa_pending') redirect(303, '/login/mfa');
	if (a.status === 'no_access' && url.pathname !== '/no-access') redirect(303, '/no-access');
	if (a.status === 'mfa_enrollment_required' && !OPEN_WHILE_ENROLLING.includes(url.pathname)) redirect(303, `${SECURITY}?required=1`);
	if (a.status === 'authenticated' && url.pathname === '/no-access') redirect(303, '/');
	if (locals.mode === 'platform' && !PLATFORM_PATHS.includes(url.pathname)) redirect(303, '/');
	const parentRoute = url.pathname === '/parent' || url.pathname.startsWith('/parent/');
	if (locals.mode === 'club' && a.status === 'authenticated' && !a.me && !parentRoute && url.pathname !== '/app' && !OPEN_WHILE_ENROLLING.includes(url.pathname)) redirect(303, '/parent');

	// Staff who open the member app: /me only says the club has a shop, not whether it is open to members right now.
	let shopOpen: boolean | null = null;
	if (parentRoute && locals.mode === 'club' && a.status === 'authenticated' && a.me?.features?.includes('shop')) {
		const access = await api<{ shop_enabled: boolean }>('GET', '/portal/access', { f: fetch });
		shopOpen = access.ok ? access.data?.shop_enabled === true : null;
	}

	return {
		me: (a.status === 'authenticated' ? a.me : null) as Me,
		clubs: locals.myClubs,
		shopOpen
	};
};
