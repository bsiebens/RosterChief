import { platform } from '#lib/api.ts';
import type { BillingSummary } from '#lib/billing.ts';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ fetch, depends }) => {
	depends('app:billing');
	const summary = await platform<BillingSummary>('GET', '/billing/summary', undefined, fetch);
	return { summary: summary.data ?? null };
};
