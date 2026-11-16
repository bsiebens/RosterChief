import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ locals }) => ({
	types: locals.auth.status === 'mfa_pending' ? locals.auth.types : []
});
