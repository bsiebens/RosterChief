import { CalendarDays, ClipboardList, House, Newspaper, ShoppingBag, UserRound, Users } from '@lucide/svelte';

export type PortalMode = 'parent' | 'coach';
export type PortalItem = { key: string; href: string; label: string; icon: typeof House };

/** The entries of the member ("parent") and coach navigation: the sidebar, the tablet rail and the phone tab bar all read this one list. */
export function portalItems(mode: PortalMode, shopEnabled: boolean): PortalItem[] {
	return mode === 'parent'
		? [
				{ key: 'home', href: '/member', label: 'portal.home', icon: House },
				{ key: 'calendar', href: '/member?tab=calendar', label: 'shell.nav.calendar', icon: CalendarDays },
				{ key: 'news', href: '/member?tab=news', label: 'shell.nav.news', icon: Newspaper },
				...(shopEnabled ? [{ key: 'shop', href: '/member/shop', label: 'shop.nav', icon: ShoppingBag }] : []),
				{ key: 'forms', href: '/member/forms', label: 'portal.forms_evaluations', icon: ClipboardList },
				{ key: 'me', href: '/member?tab=me', label: 'portal.me', icon: UserRound }
			]
		: [
				{ key: 'home', href: '/coach', label: 'portal.today', icon: House },
				{ key: 'calendar', href: '/coach?tab=calendar', label: 'shell.nav.events', icon: CalendarDays },
				{ key: 'teams', href: '/coach?tab=teams', label: 'portal.squad', icon: Users },
				{ key: 'news', href: '/coach?tab=news', label: 'shell.nav.news', icon: Newspaper }
			];
}

/**
 * The phone tab bar: members get Home, Agenda, News, Shop (only while the shop is open) and Me (Forms, Documents, Payments and the
 * shop orders live behind Me); coaches keep their four. Tablets and desktops use the longer `portalItems` list.
 */
export function portalPhoneItems(mode: PortalMode, shopEnabled = false): PortalItem[] {
	if (mode === 'coach') return portalItems('coach', false);
	return [
		{ key: 'home', href: '/member', label: 'portal.home', icon: House },
		{ key: 'calendar', href: '/member?tab=calendar', label: 'portal.agenda', icon: CalendarDays },
		{ key: 'news', href: '/member?tab=news', label: 'shell.nav.news', icon: Newspaper },
		...(shopEnabled ? [{ key: 'shop', href: '/member/shop', label: 'shop.nav', icon: ShoppingBag }] : []),
		{ key: 'me', href: '/member?tab=me', label: 'portal.me', icon: UserRound }
	];
}

/** Which entry the current location belongs to. */
export function activePortalKey(url: { pathname: string; hash: string; searchParams: { get(name: string): string | null } }): string {
	const { pathname, hash, searchParams } = url;
	if (pathname.startsWith('/member/forms')) return 'forms';
	if (pathname.startsWith('/member/profile') || pathname.startsWith('/member/calendar-sync')) return 'profile';
	if (pathname.startsWith('/member/stats')) return 'stats';
	if (pathname.startsWith('/member/notifications')) return 'notifications';
	if (pathname.startsWith('/member/shop')) return 'shop';
	if (pathname.startsWith('/coach/teams/')) return 'teams';
	if (pathname.startsWith('/coach/news')) return 'news';
	if (pathname.startsWith('/coach/notifications')) return 'home';
	if (pathname !== '/member' && pathname !== '/coach') return 'calendar';
	return searchParams.get('tab') || hash.slice(1) || 'home';
}

/** The Me entry stays selected on its sub pages. */
export const ME_PAGES = ['me', 'payments', 'documents', 'profile', 'stats', 'notifications'];
/** On a phone these also sit behind Me. */
export const ME_PHONE_PAGES = [...ME_PAGES, 'forms'];
