import { platform } from '#lib/api.ts';
import type { IssuerSettings } from '#lib/billing.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, depends }) => {
	depends('app:billing');
	const [settings, countries] = await Promise.all([platform<IssuerSettings>('GET', '/billing/settings', undefined, fetch), platform<{ code: string; name: string }[] | { items: { code: string; name: string }[] }>('GET', '/countries', undefined, fetch)]);
	const list = Array.isArray(countries.data) ? countries.data : (countries.data?.items ?? []);
	return { settings: settings.data ?? null, error: settings.ok ? '' : (settings.message ?? 'The settings could not be loaded.'), countries: list };
};
