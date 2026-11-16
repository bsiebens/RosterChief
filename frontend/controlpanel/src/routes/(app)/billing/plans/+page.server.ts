import { platform } from '#lib/api.ts';
import type { BillingPlan, IssuerSettings } from '#lib/billing.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, depends }) => {
	depends('app:billing');
	const [plans, settings] = await Promise.all([platform<BillingPlan[]>('GET', '/billing/plans', undefined, fetch), platform<IssuerSettings>('GET', '/billing/settings', undefined, fetch)]);
	return {
		plans: plans.data ?? [],
		error: plans.ok ? '' : (plans.message ?? 'The plans could not be loaded.'),
		vatRate: settings.data?.vat_rate ?? null
	};
};
