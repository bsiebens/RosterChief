import { redirect } from '@sveltejs/kit';
import { api, apiPage } from '#lib/management-api.ts';
import { APP_COOKIE } from '#lib/landing.ts';
import type { EventRow } from '#lib/events/types.ts';
import type { NewsListItem } from '#lib/news.ts';
import type { Overview } from '#lib/overview.ts';
import type { ShopStats } from '#lib/shop-orders.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url, fetch, parent, cookies, request }) => {
	if (locals.mode === 'platform') {
		// One club: no picker needed, go straight to its host (never to ourselves).
		const only = locals.myClubs.length === 1 ? locals.myClubs[0] : null;
		if (only && new URL(only.url).hostname !== url.hostname) redirect(303, `/go/${only.slug}`);
		return { overview: null as Overview | null, noTypeCount: null as number | null, upcoming: [] as EventRow[], news: [] as NewsListItem[], newsPending: 0, shop: null as ShopStats | null, shopClosed: false, registrationClosed: false };
	}
	// A failure here (no permission, backend hiccup) just hides the part that needed it.
	const { me } = (await parent()) as { me?: { member_id?: string | null; has_member_portal_role?: boolean; can_manage_members: boolean; can_add_news?: boolean; can_publish_news?: boolean; features?: string[]; can_manage_shop_products?: boolean; can_manage_shop_inventory?: boolean; can_manage_shop_orders?: boolean; can_manage_shop_discounts?: boolean; can_manage_shop_invoices?: boolean } };
	// Landing, only when the person ARRIVES here (right after signing in, or by typing / bookmarking the address) and never when they
	// click or hover a link inside the app: link prefetching would otherwise run this with the old choice and bounce them back.
	// Someone with both sides opens the app they used last; on a phone (right after signing in) staff open the coach view.
	const arriving = url.searchParams.has('landing') || request.headers.get('sec-fetch-site') === 'none';
	if (arriving) {
		const lastApp = cookies.get(APP_COOKIE);
		if (me?.has_member_portal_role && lastApp === 'member') redirect(303, '/member');
		if (url.searchParams.get('landing') === 'phone' && me?.member_id) redirect(303, '/coach');
	}
	// The shop numbers: only with the shop feature and some shop permission.
	const shopOn = !!me?.features?.includes('shop') && !!(me.can_manage_shop_products || me.can_manage_shop_inventory || me.can_manage_shop_orders || me.can_manage_shop_discounts || me.can_manage_shop_invoices);
	// The next events everyone in the app may see (the calendar API returns all of them), soonest first.
	const today = new Date();
	const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	const horizon = new Date(today.getTime() + 60 * 86_400_000);
	const [overview, noType, events, news, pending, shopStats, shopStatus, registration] = await Promise.all([
		api<Overview>('GET', '/overview', { f: fetch }),
		// People holding no role (member, staff or guardian) in the current season: an issue for member managers.
		me?.can_manage_members ? apiPage<unknown>('/members', { role: 'none', limit: 1 }, fetch) : Promise.resolve(null),
		api<EventRow[]>('GET', '/events', { query: { from: iso(today), to: iso(horizon) }, f: fetch }),
		// The latest live club news (everyone sees published items), and for publishers the review queue size.
		me?.can_add_news ? api<{ results: NewsListItem[] }>('GET', '/news', { query: { live: true, limit: 5 }, f: fetch }) : Promise.resolve(null),
		me?.can_publish_news ? api<{ count: number }>('GET', '/news', { query: { status: 'pending_review', limit: 1 }, f: fetch }) : Promise.resolve(null),
		shopOn ? api<ShopStats>('GET', '/shop/stats', { f: fetch }) : Promise.resolve(null),
		// Whether the shop is closed for members: an item for "needs attention" (public status endpoint, readable with any shop permission).
		shopOn ? api<{ open: boolean; message: string }>('GET', '/public/shop/status', { f: fetch }) : Promise.resolve(null),
		// Whether public registration is switched off: an item for "needs attention" for member managers (they own the settings page).
		me?.can_manage_members ? api<{ is_open: boolean }>('GET', '/registration/settings', { f: fetch }) : Promise.resolve(null)
	]);
	const now = Date.now();
	const upcoming = (events.ok ? (events.data ?? []) : []).filter((e) => !e.cancelled && new Date(e.end ?? e.start).getTime() >= now).slice(0, 12);
	return { shopClosed: !!shopStatus?.ok && shopStatus.data?.open === false, registrationClosed: !!registration?.ok && registration.data?.is_open === false, shop: shopStats?.ok ? (shopStats.data ?? null) : null, upcoming, news: news?.ok ? (news.data?.results ?? []) : [], newsPending: pending?.ok ? (pending.data?.count ?? 0) : 0, overview: overview.ok ? (overview.data ?? null) : null, noTypeCount: noType?.ok && noType.data ? noType.data.count : null };
};
