import type { Component } from 'svelte';
import { Banknote, CalendarDays, ClipboardCheck, ClipboardList, LayoutDashboard, MessageCircle, Newspaper, Settings, Shirt, ShoppingBag, Trophy, Users, Whistle } from '@lucide/svelte';
import type { Me } from './types.ts';

/**
 * The sidebar. TO ADD A SECTION: append an entry to NAV (label = i18n key from messages/shell.ts or your own file,
 * icon = a lucide icon, `visible` = optional predicate on /me, `children` = sub-items shown only while the section
 * is active). Sections that are not built yet (Teams, Calendar, Finance, Settings ...) are deliberately absent.
 */
/** A count shown as a small badge next to a nav entry; the sidebar fetches the numbers (see `Sidebar.svelte`). */
export type NavBadge = 'signups' | 'registrations' | 'members' | 'payments' | 'finance' | 'chat';
export type NavChild = { href: string; label: string; match?: (path: string) => boolean; visible?: (me: Me) => boolean; badge?: NavBadge };
export type NavItem = {
	key: string;
	href: string;
	label: string;
	icon: Component<{ size?: number; 'aria-hidden'?: boolean | 'true' | 'false' }>;
	/** Is the section active on this path? Defaults to a prefix match on `href`. */
	match?: (path: string) => boolean;
	visible?: (me: Me) => boolean;
	badge?: NavBadge;
	children?: NavChild[];
};

// Predicate helpers: me.permissions are Django permission strings (e.g. 'members.manage_members').
export const can = (me: Me, perm: string) => me.is_club_admin || me.permissions.includes(perm);

export const NAV: NavItem[] = [
	{ key: 'overview', href: '/', label: 'shell.nav.overview', icon: LayoutDashboard, match: (p) => p === '/' },
	{
		key: 'members',
		href: '/members',
		label: 'shell.nav.members',
		icon: Users,
		match: (p) => p.startsWith('/members') || p.startsWith('/families') || p.startsWith('/groups') || p.startsWith('/signups'),
		badge: 'members',
		children: [
			{ href: '/members', label: 'shell.nav.all_members', match: (p) => p.startsWith('/members') },
			{ href: '/signups', label: 'signups.nav', match: (p) => p.startsWith('/signups'), visible: (me) => me.can_manage_members, badge: 'signups' },
			{ href: '/families', label: 'shell.nav.families', match: (p) => p.startsWith('/families'), visible: (me) => me.can_manage_members },
			{ href: '/groups', label: 'shell.nav.groups', match: (p) => p.startsWith('/groups'), visible: (me) => me.can_manage_members || (me.admin_group_ids?.length ?? 0) > 0 }
		]
	},
	{
		key: 'teams',
		href: '/teams',
		label: 'shell.nav.teams',
		icon: Shirt,
		match: (p) => p === '/teams' || p.startsWith('/teams/'),
		children: [
			{ href: '/teams', label: 'shell.nav.teams_list', match: (p) => p === '/teams' || (p.startsWith('/teams/') && !p.startsWith('/teams/numbers') && !p.startsWith('/teams/officials')) },
			{ href: '/teams/numbers', label: 'shell.nav.teams_numbers', match: (p) => p.startsWith('/teams/numbers') },
			{ href: '/teams/officials', label: 'shell.nav.teams_officials', match: (p) => p.startsWith('/teams/officials'), visible: (me) => me.features.includes('referee_officials') }
		]
	},
	{
		key: 'calendar',
		href: '/calendar',
		label: 'shell.nav.calendar',
		icon: CalendarDays,
		match: (p) => p === '/calendar' || p.startsWith('/calendar/'),
		children: [
			{ href: '/calendar', label: 'shell.nav.events', match: (p) => p === '/calendar' || p === '/calendar/events' || p.startsWith('/calendar/events/') },
			{ href: '/calendar/locations', label: 'shell.nav.locations', match: (p) => p.startsWith('/calendar/locations') },
			{ href: '/calendar/opponents', label: 'shell.nav.opponents', match: (p) => p.startsWith('/calendar/opponents') }
		]
	},
	{
		key: 'news',
		href: '/news',
		label: 'shell.nav.news',
		icon: Newspaper,
		match: (p) => p === '/news' || p.startsWith('/news/'),
		visible: (me) => me.can_add_news
	},
	{
		key: 'chat',
		href: '/chat',
		label: 'chat.nav',
		icon: MessageCircle,
		match: (p) => p === '/chat' || p.startsWith('/chat/'),
		visible: (me) => me.features.includes('chat') && me.can_use_chat === true,
		badge: 'chat'
	},
	{
		key: 'forms',
		href: '/forms',
		label: 'forms.nav',
		icon: ClipboardList,
		match: (p) => p === '/forms' || p.startsWith('/forms/'),
		visible: (me) => me.can_manage_forms,
		children: [
			{ href: '/forms', label: 'forms.nav.forms', match: (p) => p === '/forms' || (p.startsWith('/forms/') && !p.startsWith('/forms/sends')) },
			{ href: '/forms/sends', label: 'forms.nav.sends', match: (p) => p.startsWith('/forms/sends') }
		]
	},
	{
		key: 'evaluations',
		href: '/evaluations',
		label: 'eval.nav',
		icon: ClipboardCheck,
		match: (p) => p === '/evaluations' || p.startsWith('/evaluations/'),
		visible: (me) => me.features.includes('evaluations') && (me.can_execute_evaluations || me.can_manage_evaluations)
	},
	{
		key: 'shop',
		href: '/shop',
		label: 'shop.nav',
		icon: ShoppingBag,
		match: (p) => p === '/shop' || p.startsWith('/shop/'),
		visible: (me) => me.features.includes('shop') && (me.can_manage_shop_products || me.can_manage_shop_inventory || me.can_manage_shop_orders || me.can_manage_shop_discounts || me.can_manage_shop_invoices),
		// Keep this order: products, inventory, orders (production, ready for pickup and the delivery desk are buttons on the orders page), discounts, vouchers, invoices.
		children: [
			{ href: '/shop/products', label: 'shop.nav.products', match: (p) => p.startsWith('/shop/products'), visible: (me) => me.can_manage_shop_products },
			{ href: '/shop/inventory', label: 'shop.nav.inventory', match: (p) => p.startsWith('/shop/inventory'), visible: (me) => me.can_manage_shop_inventory },
			{ href: '/shop/orders', label: 'shop.nav.orders', match: (p) => ['/shop/orders', '/shop/production', '/shop/pickup', '/shop/delivery'].some((x) => p.startsWith(x)), visible: (me) => me.can_manage_shop_orders },
			{ href: '/shop/discounts', label: 'shop.nav.discounts', match: (p) => p.startsWith('/shop/discounts'), visible: (me) => me.can_manage_shop_discounts },
			{ href: '/shop/vouchers', label: 'shop.nav.vouchers', match: (p) => p.startsWith('/shop/vouchers'), visible: (me) => me.can_manage_shop_discounts },
			{ href: '/shop/invoices', label: 'shop.nav.invoices', match: (p) => p.startsWith('/shop/invoices'), visible: (me) => me.can_manage_shop_invoices },
			{ href: '/shop/settings', label: 'shop.nav.settings', match: (p) => p.startsWith('/shop/settings'), visible: (me) => me.can_manage_shop_orders }
		]
	},
	{
		key: 'tournaments',
		href: '/tournaments-admin',
		label: 'tn.nav',
		icon: Trophy,
		match: (p) => p === '/tournaments-admin' || p.startsWith('/tournaments-admin/'),
		visible: (me) => me.features.includes('tournaments') && (me.can_manage_tournaments || me.can_operate_tournaments),
		children: [
			{ href: '/tournaments-admin', label: 'tn.nav.list', match: (p) => p === '/tournaments-admin' || (p.startsWith('/tournaments-admin/') && !p.startsWith('/tournaments-admin/dressing-rooms')) },
			{ href: '/tournaments-admin/dressing-rooms', label: 'tn.nav.dressing_rooms', match: (p) => p.startsWith('/tournaments-admin/dressing-rooms'), visible: (me) => me.can_manage_tournaments }
		]
	},
	{
		key: 'referees',
		href: '/referees',
		label: 'shell.nav.referees',
		icon: Whistle,
		match: (p) => p === '/referees' || p.startsWith('/referees/'),
		visible: (me) => me.can_manage_referees && me.features.includes('referee_officials')
	},
	{
		key: 'finance',
		href: '/finance',
		label: 'fin.nav',
		icon: Banknote,
		match: (p) => p.startsWith('/finance') || p.startsWith('/registrations'),
		visible: (me) => me.can_manage_finance || me.can_manage_sponsors || me.can_manage_charges || (me.features.includes('shop') && me.can_manage_shop_invoices),
		badge: 'finance',
		children: [
			{ href: '/finance', label: 'fin.nav.payments', match: (p) => p.startsWith('/finance') && !p.startsWith('/finance/sponsors') && !p.startsWith('/finance/charges') && !p.startsWith('/finance/invoices'), visible: (me) => me.can_manage_finance, badge: 'payments' },
			{ href: '/registrations', label: 'reg.nav', match: (p) => p.startsWith('/registrations'), visible: (me) => me.can_manage_finance, badge: 'registrations' },
			{ href: '/finance/invoices', label: 'fin.nav.invoices', match: (p) => p.startsWith('/finance/invoices') || p.startsWith('/finance/charges'), visible: (me) => me.can_manage_finance || me.can_manage_charges || (me.features.includes('shop') && me.can_manage_shop_invoices) },
			{ href: '/finance/sponsors', label: 'sponsors.title', match: (p) => p.startsWith('/finance/sponsors'), visible: (me) => me.can_manage_sponsors }
		]
	},
	{
		key: 'settings',
		href: '/settings',
		label: 'shell.nav.settings',
		icon: Settings,
		match: (p) => p === '/settings' || p.startsWith('/settings/'),
		visible: (me) => me.can_manage_settings || me.can_manage_roles || me.can_manage_members,
		children: [
			{ href: '/settings', label: 'shell.nav.settings_general', match: (p) => p === '/settings' || p.startsWith('/settings/preview'), visible: (me) => me.can_manage_settings },
			{ href: '/settings/positions', label: 'shell.nav.positions', match: (p) => p.startsWith('/settings/positions'), visible: (me) => me.can_manage_settings },
			{ href: '/settings/event-types', label: 'shell.nav.event_types', match: (p) => p.startsWith('/settings/event-types'), visible: (me) => me.can_manage_settings },
			{ href: '/settings/number-pools', label: 'shell.nav.number_pools', match: (p) => p.startsWith('/settings/number-pools'), visible: (me) => me.can_manage_settings },
			{ href: '/settings/officiating', label: 'shell.nav.officiating', match: (p) => p.startsWith('/settings/officiating'), visible: (me) => me.can_manage_settings && me.features.includes('referee_officials') },
			{ href: '/settings/registration', label: 'regset.nav', match: (p) => p.startsWith('/settings/registration'), visible: (me) => me.can_manage_members },
			{ href: '/settings/requirements', label: 'req.nav', match: (p) => p.startsWith('/settings/requirements'), visible: (me) => me.can_manage_members },
			{ href: '/settings/roles', label: 'roles.nav', match: (p) => p.startsWith('/settings/roles'), visible: (me) => me.can_manage_roles }
		]
	}
];

export const isActive = (item: NavItem, path: string) => (item.match ? item.match(path) : path === item.href || path.startsWith(`${item.href}/`));

export function visibleNav(me: Me): NavItem[] {
	return NAV.filter((i) => !i.visible || i.visible(me)).map((i) => ({
		...i,
		href: i.key === 'finance' && !me.can_manage_finance ? (me.can_manage_charges || (me.features.includes('shop') && me.can_manage_shop_invoices) ? '/finance/invoices' : '/finance/sponsors') : i.href,
		children: i.children?.filter((c) => !c.visible || c.visible(me))
	}));
}
