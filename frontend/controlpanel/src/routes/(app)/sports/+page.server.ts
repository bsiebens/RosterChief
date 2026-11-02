import { platform, platformList } from '#lib/api.ts';
import type { CatalogFeature } from '#lib/features.ts';
import { NO_SPORT, type Competition, type RefereeType, type Sport } from '#lib/sports.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, url, depends }) => {
	depends('app:sports');
	const [sports, competitions, features] = await Promise.all([
		platformList<Sport>('/sports', fetch),
		platformList<Competition>('/competitions', fetch),
		platform<CatalogFeature[]>('GET', '/features', undefined, fetch)
	]);
	const list = sports.data ?? [];
	const asked = url.searchParams.get('sport') ?? '';
	const selected = asked === NO_SPORT ? NO_SPORT : (list.find((s) => s.id === asked)?.id ?? list[0]?.id ?? NO_SPORT);

	let types: RefereeType[] = [];
	let typesError = '';
	if (selected !== NO_SPORT) {
		const r = await platform<RefereeType[]>('GET', `/sports/${selected}/referee-types`, undefined, fetch);
		types = r.data ?? [];
		if (!r.ok) typesError = r.message ?? 'The referee types could not be loaded.';
	}

	return {
		sports: list,
		sportsError: sports.ok ? '' : (sports.message ?? 'The sports could not be loaded.'),
		competitions: competitions.data ?? [],
		competitionsError: competitions.ok ? '' : (competitions.message ?? 'The competitions could not be loaded.'),
		features: features.data ?? [],
		selected,
		types,
		typesError
	};
};
