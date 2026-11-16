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
	// The number on each tab follows the same search and "mine" filters, so it matches what the tab shows; the active tab reuses the list's own count.
	const countOf = (key: Tab) => api<NewsList>('GET', '/news', { query: { status: STATUS[key], mine: mine ? 'true' : undefined, q, limit: 1 }, f: fetch });
	const [list, all, review, draft, published, teams, pendingAll] = await Promise.all([
		api<NewsList>('GET', '/news', { query: { status: STATUS[tab], mine: mine ? 'true' : undefined, q, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch }),
		tab === 'all' ? Promise.resolve(null) : countOf('all'),
		me.can_publish_news && tab !== 'review' ? countOf('review') : Promise.resolve(null),
		tab === 'draft' ? Promise.resolve(null) : countOf('draft'),
		tab === 'published' ? Promise.resolve(null) : countOf('published'),
		api<TeamOption[]>('GET', '/news/team-options', { f: fetch }),
		// the review banner counts everything waiting, whatever the search or "mine" filters say
		me.can_publish_news ? api<NewsList>('GET', '/news', { query: { status: 'pending_review', limit: 1 }, f: fetch }) : Promise.resolve(null)
	]);
	const own = list.data?.count ?? 0;
	const counts: Record<Tab, number> = {
		all: tab === 'all' ? own : (all?.data?.count ?? 0),
		review: tab === 'review' ? own : (review?.data?.count ?? 0),
		draft: tab === 'draft' ? own : (draft?.data?.count ?? 0),
		published: tab === 'published' ? own : (published?.data?.count ?? 0)
	};
	return {
		filters: { q, tab, mine, offset },
		rows: list.data?.results ?? [],
		count: list.data?.count ?? 0,
		reviewCount: pendingAll?.data?.count ?? 0,
		counts,
		teams: teams.data ?? [],
		pageSize: PAGE_SIZE,
		error: list.ok ? null : list.status
	};
};
