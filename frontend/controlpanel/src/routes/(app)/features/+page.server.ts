import { platform, platformList } from '#lib/api.ts';
import type { ClubOption, CatalogFeature, FullPlan, LogEntry, Switches } from '#lib/features.ts';
import type { Page } from '#lib/clubs.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ fetch, url, depends }) => {
	depends('app:features');
	const club = url.searchParams.get('club') ?? '';
	const page = Math.max(1, Number(url.searchParams.get('page')) || 1);
	const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
	if (club) params.set('club_id', club);

	const [switches, plans, features, log, clubs] = await Promise.all([
		platform<Switches>('GET', '/switches', undefined, fetch),
		platformList<FullPlan>('/plans', fetch),
		platform<CatalogFeature[]>('GET', '/features', undefined, fetch),
		platform<Page<LogEntry>>('GET', `/entitlement-log?${params}`, undefined, fetch),
		platformList<{ id: string; name: string }>('/clubs?status=all', fetch)
	]);

	return {
		switches: switches.data ?? null,
		switchesError: switches.ok ? '' : (switches.message ?? 'The switches could not be loaded.'),
		plans: plans.data ?? [],
		plansError: plans.ok ? '' : (plans.message ?? 'The plans could not be loaded.'),
		features: features.data ?? [],
		featuresError: features.ok ? '' : (features.message ?? 'The feature catalog could not be loaded.'),
		log: log.data?.items ?? [],
		logTotal: log.data?.count ?? 0,
		logError: log.ok ? '' : (log.message ?? 'The entitlement log could not be loaded.'),
		clubs: (clubs.data ?? []).map((c): ClubOption => ({ id: c.id, name: c.name })),
		query: { club, page },
		pageSize: PAGE_SIZE
	};
};
