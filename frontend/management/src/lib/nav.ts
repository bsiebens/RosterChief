import type { Component } from 'svelte';
import { LayoutDashboard, Settings, Shirt, Users } from '@lucide/svelte';
import type { Me } from './types.ts';

/**
 * The sidebar. TO ADD A SECTION: append an entry to NAV (label = i18n key from messages/shell.ts or your own file,
 * icon = a lucide icon, `visible` = optional predicate on /me, `children` = sub-items shown only while the section
 * is active). Sections that are not built yet (Teams, Calendar, Finance, Settings ...) are deliberately absent.
 */
export type NavChild = { href: string; label: string; match?: (path: string) => boolean; visible?: (me: Me) => boolean };
export type NavItem = {
	key: string;
	href: string;
	label: string;
	icon: Component<{ size?: number; 'aria-hidden'?: boolean | 'true' | 'false' }>;
	/** Is the section active on this path? Defaults to a prefix match on `href`. */
	match?: (path: string) => boolean;
	visible?: (me: Me) => boolean;
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
		match: (p) => p.startsWith('/members') || p.startsWith('/families') || p.startsWith('/groups'),
		children: [
			{ href: '/members', label: 'shell.nav.all_members', match: (p) => p.startsWith('/members') },
			{ href: '/families', label: 'shell.nav.families', match: (p) => p.startsWith('/families'), visible: (me) => me.can_manage_members },
			{ href: '/groups', label: 'shell.nav.groups', match: (p) => p.startsWith('/groups'), visible: (me) => me.can_manage_members }
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
		key: 'settings',
		href: '/settings',
		label: 'shell.nav.settings',
		icon: Settings,
		match: (p) => p === '/settings' || p.startsWith('/settings/'),
		visible: (me) => me.can_manage_settings,
		children: [
			{ href: '/settings', label: 'shell.nav.settings_general', match: (p) => p === '/settings' || p.startsWith('/settings/preview') },
			{ href: '/settings/positions', label: 'shell.nav.positions', match: (p) => p.startsWith('/settings/positions') },
			{ href: '/settings/number-pools', label: 'shell.nav.number_pools', match: (p) => p.startsWith('/settings/number-pools') },
			{ href: '/settings/officiating', label: 'shell.nav.officiating', match: (p) => p.startsWith('/settings/officiating'), visible: (me) => me.features.includes('referee_officials') }
		]
	}
];

export const isActive = (item: NavItem, path: string) => (item.match ? item.match(path) : path === item.href || path.startsWith(`${item.href}/`));

export function visibleNav(me: Me): NavItem[] {
	return NAV.filter((i) => !i.visible || i.visible(me)).map((i) => ({ ...i, children: i.children?.filter((c) => !c.visible || c.visible(me)) }));
}
