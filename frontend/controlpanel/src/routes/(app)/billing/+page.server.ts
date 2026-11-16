import { platform, platformList } from '#lib/api.ts';
import type { BillingPlan, Due } from '#lib/billing.ts';
import type { ClubRef } from '#lib/billing.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const STATUSES = ['owing', 'unpaid', 'partial', 'paid', 'waived', 'cancelled', 'all'];
const STATES = ['overdue', 'in_grace', 'issued_ahead', 'settled'];

export const load: PageServerLoad = async ({ fetch, url, depends }) => {
	depends('app:billing');
	const sp = url.searchParams;
	const search = sp.get('q')?.trim() ?? '';
	const status = STATUSES.includes(sp.get('status') ?? '') ? sp.get('status')! : 'owing';
	const state = STATES.includes(sp.get('state') ?? '') ? sp.get('state')! : '';
	const club = sp.get('club') ?? '';
	const plan = sp.get('plan') ?? '';
	const page = Math.max(1, Number(sp.get('page')) || 1);

	const params = new URLSearchParams({ status, limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
	if (state) params.set('state', state);
	if (club) params.set('club_id', club);
	if (plan) params.set('plan_id', plan);
	if (search) params.set('search', search);

	const [dues, plans, clubs] = await Promise.all([
		platform<{ items: Due[]; count: number }>('GET', `/billing/dues?${params}`, undefined, fetch),
		platform<BillingPlan[]>('GET', '/billing/plans', undefined, fetch),
		platformList<ClubRef>('/clubs?status=all', fetch)
	]);

	return {
		dues: dues.data?.items ?? [],
		total: dues.data?.count ?? 0,
		error: dues.ok ? '' : (dues.message ?? 'The billing periods could not be loaded.'),
		plans: (plans.data ?? []).map((p) => p.plan),
		clubs: clubs.data ?? [],
		query: { q: search, status, state, club, plan, page },
		pageSize: PAGE_SIZE
	};
};
