import { platform, platformList } from '#lib/api.ts';
import type { ClubRef, CreditNote } from '#lib/billing.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ fetch, url, depends }) => {
	depends('app:billing');
	const sp = url.searchParams;
	const search = sp.get('q')?.trim() ?? '';
	const club = sp.get('club') ?? '';
	const page = Math.max(1, Number(sp.get('page')) || 1);

	const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
	if (club) params.set('club_id', club);
	if (search) params.set('search', search);

	const [notes, clubs] = await Promise.all([
		platform<{ items: CreditNote[]; count: number }>('GET', `/billing/credit-notes?${params}`, undefined, fetch),
		platformList<ClubRef>('/clubs?status=all', fetch)
	]);

	return {
		notes: notes.data?.items ?? [],
		total: notes.data?.count ?? 0,
		error: notes.ok ? '' : (notes.message ?? 'The credit notes could not be loaded.'),
		clubs: clubs.data ?? [],
		query: { q: search, club, page },
		pageSize: PAGE_SIZE
	};
};
