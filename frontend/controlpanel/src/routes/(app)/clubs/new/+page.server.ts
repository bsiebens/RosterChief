import { platform, platformList } from '#lib/api.ts';
import type { Country, Sport } from '#lib/clubs.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [sports, countries] = await Promise.all([platformList<Sport>('/sports', fetch), platform<Country[]>('GET', '/countries', undefined, fetch)]);
	return { sports: sports.data ?? [], countries: countries.data ?? [] };
};
