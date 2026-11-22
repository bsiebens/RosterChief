import { loadOr } from '#lib/tournaments-server.ts';
import type { PageServerLoad } from './$types';

export type SponsorDoc = { id: string; name: string; url: string; logo_url: string | null; position: number };
export type SponsorsDoc = { use_club_sponsors: boolean; club_sponsor_count: number; results: SponsorDoc[] };

export const load: PageServerLoad = async ({ fetch, parent, params }) => {
	await parent();
	const { data, status } = await loadOr<SponsorsDoc>(fetch, `/tournaments/${params.id}/sponsors`);
	return { sponsors: data, error: data ? null : status };
};
