import { api } from '#lib/management-api.ts';
import type { NewsList, TeamOption } from '#lib/news.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const TABS = ['all', 'review', 'draft', 'published'] as const;
type Tab = (typeof TABS)[number];
const STATUS: Record<Tab, string> = { all: '', review: 'pending_review', draft: 'draft', published: 'published' };

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	const q = (url.searchParams.get('q') ?? '').trim();
	const requested = url.searchParams.get('tab') as Tab;
	// "Needs review" only makes sense for people who can publish; the API also hides other people's pending items from the rest.
	const tab: Tab = TABS.includes(requested) && (requested !== 'review' || me.can_publish_news) ? requested : 'all';
	const mine = url.searchParams.get('mine') === '1';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const [list, review, teams] = await Promise.all([
		api<NewsList>('GET', '/news', { query: { status: STATUS[tab], mine: mine ? 'true' : undefined, q, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch }),
		me.can_publish_news ? api<NewsList>('GET', '/news', { query: { status: 'pending_review', limit: 1 }, f: fetch }) : Promise.resolve(null),
		api<TeamOption[]>('GET', '/news/team-options', { f: fetch })
	]);
	return {
		filters: { q, tab, mine, offset },
		rows: list.data?.results ?? [],
		count: list.data?.count ?? 0,
		reviewCount: review?.data?.count ?? 0,
		teams: teams.data ?? [],
		pageSize: PAGE_SIZE,
		error: list.ok ? null : list.status
	};
};
