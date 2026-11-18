import { platform, platformList } from '#lib/api.ts';
import type { ClubRow, Page, Provisioning, Sport } from '#lib/clubs.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const RECENT_FAILURE_DAYS = 3;

export const load: PageServerLoad = async ({ fetch, url }) => {
	const q = url.searchParams.get('q')?.trim() ?? '';
	const status = ['active', 'archived', 'all'].includes(url.searchParams.get('status') ?? '') ? url.searchParams.get('status')! : 'active';
	const sport = url.searchParams.get('sport') ?? '';
	const page = Math.max(1, Number(url.searchParams.get('page')) || 1);

	const params = new URLSearchParams({ status, limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
	if (q) params.set('q', q);
	if (sport) params.set('sport_id', sport);

	// The numbers on the status filter follow the search and sport filters, never the status itself.
	const countParams = new URLSearchParams();
	if (q) countParams.set('q', q);
	if (sport) countParams.set('sport_id', sport);

	const [clubs, counts, sports, provisioning] = await Promise.all([
		platform<Page<ClubRow>>('GET', `/clubs?${params}`, undefined, fetch),
		platform<{ active: number; archived: number; all: number }>('GET', `/clubs/counts?${countParams}`, undefined, fetch),
		platformList<Sport>('/sports', fetch),
		platform<Page<Provisioning>>('GET', '/club-provisioning?limit=10', undefined, fetch)
	]);

	const cutoff = Date.now() - RECENT_FAILURE_DAYS * 86_400_000;
	return {
		clubs: clubs.data?.items ?? [],
		total: clubs.data?.count ?? 0,
		counts: counts.data ?? null,
		error: clubs.ok ? '' : (clubs.message ?? 'The clubs could not be loaded.'),
		sports: sports.data ?? [],
		// Requests that are still being built, or that failed recently; finished ones are just clubs in the list.
		requests: (provisioning.data?.items ?? []).filter((j) => j.status === 'pending' || j.status === 'running' || (j.status === 'failed' && new Date(j.created).getTime() > cutoff)),
		query: { q, status, sport, page },
		pageSize: PAGE_SIZE
	};
};
