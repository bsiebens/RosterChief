<script lang="ts">
	import { page } from '$app/state';
	import { Bell, CalendarDays, ClipboardList, House, Newspaper, ShoppingBag, UserRound, Users, Wallet } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	let { mode, compact = false, shopEnabled = false }: { mode: 'parent' | 'coach'; compact?: boolean; shopEnabled?: boolean } = $props();
	const items = $derived(mode === 'parent'
		? [
			{ key: 'home', href: '/parent', label: 'portal.home', icon: House },
			{ key: 'calendar', href: '/parent?tab=calendar', label: 'shell.nav.calendar', icon: CalendarDays },
			{ key: 'news', href: '/parent?tab=news', label: 'shell.nav.news', icon: Newspaper },
			...(shopEnabled ? [{ key: 'shop', href: '/parent/shop', label: 'shop.nav', icon: ShoppingBag }] : []),
			{ key: 'me', href: '/parent?tab=me', label: 'portal.me', icon: UserRound }
		]
		: [
			{ key: 'home', href: '/coach', label: 'portal.today', icon: House },
			{ key: 'calendar', href: '/coach?tab=calendar', label: 'shell.nav.events', icon: CalendarDays },
			{ key: 'teams', href: '/coach?tab=teams', label: 'portal.squad', icon: Users },
			{ key: 'news', href: '/coach?tab=news', label: 'shell.nav.news', icon: Newspaper }
		]);
	const active = $derived.by(() => {
		const { pathname, hash, searchParams } = page.url;
		if (pathname.startsWith('/parent/forms/')) return 'forms';
		if (pathname.startsWith('/parent/profile')) return 'profile';
		if (pathname.startsWith('/parent/notifications')) return 'notifications';
		if (pathname.startsWith('/parent/shop')) return 'shop';
		if (pathname.startsWith('/coach/teams/')) return 'teams';
		if (pathname !== '/parent' && pathname !== '/coach') return 'calendar';
		return searchParams.get('tab') || hash.slice(1) || 'home';
	});
	const meActive = $derived(['me', 'forms', 'payments', 'profile', 'notifications'].includes(active));
</script>

<nav class={compact ? 'portal-tabs grid auto-cols-fr grid-flow-col' : 'flex flex-col gap-0.5 px-2.5'} aria-label={t('shell.nav.label')}>
	{#each items as item (item.href)}
		{@const selected = active === item.key || (item.key === 'me' && meActive)}
		<a class="{compact ? 'portal-tab' : 'nav-item'} {selected ? 'active' : ''}" href={item.href} aria-current={selected ? 'page' : undefined}>
			<item.icon size={compact ? 21 : 17} class="shrink-0" aria-hidden="true" />
			{t(item.label)}
		</a>
		{#if !compact && item.key === 'me' && meActive}
			<a class="nav-subitem {active === 'profile' ? 'active' : ''}" href="/parent/profile"><UserRound size={14} aria-hidden="true" /> {t('portal.profile')}</a>
			<a class="nav-subitem {active === 'notifications' ? 'active' : ''}" href="/parent/notifications"><Bell size={14} aria-hidden="true" /> {t('portal.notifications')}</a>
			<a class="nav-subitem {active === 'payments' ? 'active' : ''}" href="/parent?tab=payments"><Wallet size={14} aria-hidden="true" /> {t('portal.payments')}</a>
			<a class="nav-subitem {active === 'forms' ? 'active' : ''}" href="/parent?tab=forms"><ClipboardList size={14} aria-hidden="true" /> {t('portal.forms')}</a>
		{/if}
	{/each}
</nav>
