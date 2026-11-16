import { PLATFORM_PREFIX } from '#lib/server/proxy.ts';
import type { Dashboard } from '#lib/dashboard.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ fetch }) => ({
	// Not awaited: the snapshot walks every club schema, so the page shell renders first and the numbers stream in.
	dashboard: fetch(`${PLATFORM_PREFIX}stats/dashboard`)
		.then(async (res) => (res.ok ? { data: (await res.json()) as Dashboard } : { error: `The statistics service answered ${res.status}.` }))
		.catch(() => ({ error: 'The statistics could not be loaded.' }))
});
