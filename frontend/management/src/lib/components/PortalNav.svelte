<script lang="ts">
	import { page } from '$app/state';
	import { ChartColumn, UserRound, Wallet, Bell } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { activePortalKey, ME_PAGES, portalItems, type PortalMode } from '#lib/portal-nav.ts';

	let { mode, compact = false, shopEnabled = false }: { mode: PortalMode; compact?: boolean; shopEnabled?: boolean } = $props();
	const items = $derived(portalItems(mode, shopEnabled));
	const active = $derived(activePortalKey(page.url));
	const meActive = $derived(ME_PAGES.includes(active));
</script>

<nav class={compact ? 'portal-tabs grid auto-cols-[minmax(0,1fr)] grid-flow-col' : 'flex flex-col gap-0.5 px-2.5'} aria-label={t('shell.nav.label')}>
	{#each items as item (item.href)}
		{@const selected = active === item.key || (item.key === 'me' && meActive)}
		<a class="{compact ? 'portal-tab' : 'nav-item'} {selected ? 'active' : ''}" href={item.href} aria-current={selected ? 'page' : undefined}>
			<item.icon size={compact ? 21 : 17} class="shrink-0" aria-hidden="true" />
			{t(item.label)}
		</a>
		{#if !compact && item.key === 'me' && meActive}
			<a class="nav-subitem {active === 'profile' ? 'active' : ''}" href="/parent/profile"><UserRound size={14} aria-hidden="true" /> {t('portal.profile')}</a>
			<a class="nav-subitem {active === 'stats' ? 'active' : ''}" href="/parent/stats"><ChartColumn size={14} aria-hidden="true" /> {t('portal.stats_page')}</a>
			<a class="nav-subitem {active === 'notifications' ? 'active' : ''}" href="/parent/notifications"><Bell size={14} aria-hidden="true" /> {t('portal.notifications')}</a>
			<a class="nav-subitem {active === 'payments' ? 'active' : ''}" href="/parent?tab=payments"><Wallet size={14} aria-hidden="true" /> {t('portal.payments')}</a>
		{/if}
	{/each}
</nav>
