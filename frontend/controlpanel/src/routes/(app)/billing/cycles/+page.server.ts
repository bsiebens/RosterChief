import { platform } from '#lib/api.ts';
import type { Cycle, InvoicingOverview } from '#lib/billing.ts';
import type { PlanLite, Unbilled } from '#lib/components/billing/cycles/types.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, depends }) => {
	depends('app:billing');
	const [cycles, unbilled, plans, settings, invoicing] = await Promise.all([
		platform<Cycle[]>('GET', '/billing/cycles', undefined, fetch),
		platform<Unbilled[]>('GET', '/billing/unbilled', undefined, fetch),
		platform<PlanLite[]>('GET', '/billing/plans', undefined, fetch),
		platform<{ archive_after_days: number; invoice_next_number_preview?: string }>('GET', '/billing/settings', undefined, fetch),
		platform<InvoicingOverview>('GET', '/billing/invoicing?ahead_days=7', undefined, fetch)
	]);
	return {
		cycles: cycles.data ?? [],
		cyclesError: cycles.ok ? '' : (cycles.message ?? 'The renewals could not be loaded.'),
		unbilled: unbilled.data ?? [],
		unbilledError: unbilled.ok ? '' : (unbilled.message ?? 'The unbilled clubs could not be loaded.'),
		plans: plans.data ?? [],
		archiveAfterDays: settings.data?.archive_after_days ?? 30,
		nextNumber: settings.data?.invoice_next_number_preview ?? null,
		invoicing: invoicing.data ?? null
	};
};
