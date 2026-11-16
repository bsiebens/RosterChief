import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ locals }) => ({ reason: locals.hostError ?? 'not_found' });
