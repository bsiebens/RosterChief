import { error } from '@sveltejs/kit';
import { platform, platformList } from '#lib/api.ts';
import type { ClubBilling, ClubDetail, ClubOverview, ClubStats, Country, Entitlement, Override, Page, Plan, Sport, Subscription } from '#lib/clubs.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, depends }) => {
	depends('app:club');
	const id = encodeURIComponent(params.id);
	const club = await platform<ClubDetail>('GET', `/clubs/${id}`, undefined, fetch);
	if (club.status === 404 || club.status === 422) error(404, 'Club not found');
	if (!club.ok) error(500, club.message ?? 'The club could not be loaded');

	const [stats, subscriptions, entitlements, overrides, billing, plans, sports, overview, countries, settings] = await Promise.all([
		platform<ClubStats>('GET', `/clubs/${id}/stats`, undefined, fetch),
		platform<Page<Subscription>>('GET', `/clubs/${id}/subscriptions?limit=50`, undefined, fetch),
		platform<Entitlement[]>('GET', `/clubs/${id}/entitlements`, undefined, fetch),
		platform<Override[]>('GET', `/clubs/${id}/overrides`, undefined, fetch),
		platform<ClubBilling>('GET', `/clubs/${id}/billing`, undefined, fetch),
		platformList<Plan>('/plans', fetch),
		platformList<Sport>('/sports', fetch),
		platform<ClubOverview>('GET', `/clubs/${id}/overview`, undefined, fetch),
		platform<Country[]>('GET', '/countries', undefined, fetch),
		platform<{ archive_after_days: number }>('GET', '/billing/settings', undefined, fetch)
	]);

	return {
		club: club.data!,
		stats: stats.data ?? null,
		subscriptions: subscriptions.data?.items ?? [],
		entitlements: entitlements.data ?? [],
		overrides: overrides.data ?? [],
		billing: billing.data ?? null,
		plans: plans.data ?? [],
		sports: sports.data ?? [],
		overview: overview.data ?? null,
		countries: countries.data ?? [],
		archiveAfterDays: settings.data?.archive_after_days ?? 30
	};
};
