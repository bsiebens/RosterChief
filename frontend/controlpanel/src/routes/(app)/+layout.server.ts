import { redirect } from '@sveltejs/kit';
import type { JobsStatus } from '#lib/dashboard.ts';
import type { LayoutServerLoad } from './$types';

const SECURITY = '/account/security';
const OPEN_WHILE_ENROLLING = [SECURITY, '/account/password'];

export const load: LayoutServerLoad = async ({ locals, url, fetch }) => {
	const a = locals.auth;
	if (a.status === 'anonymous') redirect(303, `/login${url.pathname !== '/' ? `?next=${encodeURIComponent(url.pathname + url.search)}` : ''}`);
	if (a.status === 'mfa_pending') redirect(303, '/login/mfa');
	if (a.status === 'no_access' && url.pathname !== '/no-access') redirect(303, '/no-access');
	if (a.status === 'mfa_enrollment_required' && !OPEN_WHILE_ENROLLING.includes(url.pathname)) redirect(303, `${SECURITY}?required=1`);
	if (a.status === 'authenticated' && url.pathname === '/no-access') redirect(303, '/');

	// Health of the background jobs for the top bar; a failure here must never block the page.
	let jobsStatus: JobsStatus | null = null;
	if (a.status === 'authenticated') {
		jobsStatus = await fetch('/api/platform/v1/jobs/status')
			.then((r) => (r.ok ? (r.json() as Promise<JobsStatus>) : null))
			.catch(() => null);
	}
	return { jobsStatus };
};
