import { platform } from '#lib/api.ts';
import type { PeriodicJob } from '#lib/dashboard.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const r = await platform<PeriodicJob[]>('GET', '/jobs/scheduled', undefined, fetch);
	return { jobs: r.data ?? [], error: r.ok ? '' : (r.message ?? 'The jobs could not be loaded.') };
};
