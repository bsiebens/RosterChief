import { redirect } from '@sveltejs/kit';
import type { LayoutServerLoad } from './$types';

/** Entrance screens are for visitors who are not signed in yet. */
export const load: LayoutServerLoad = ({ locals, url }) => {
	const a = locals.auth;
	const onMfaStep = url.pathname.startsWith('/login/mfa');
	if (a.status === 'authenticated' || a.status === 'mfa_enrollment_required' || a.status === 'no_access') {
		// Password-reset and verify-email links work whether or not you are signed in.
		if (!url.pathname.startsWith('/account/')) redirect(303, '/');
	}
	if (a.status === 'mfa_pending' && !onMfaStep && url.pathname.startsWith('/login')) redirect(303, '/login/mfa');
	if (a.status !== 'mfa_pending' && onMfaStep) redirect(303, '/login');
	return {};
};
