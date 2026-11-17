<script lang="ts">
	import { page } from '$app/state';
	import { ChartColumn, UserRound, Wallet, Bell } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { activePortalKey, ME_PAGES, ME_PHONE_PAGES, portalItems, portalPhoneItems, type PortalMode } from '#lib/portal-nav.ts';

	let { mode, compact = false, shopEnabled = false }: { mode: PortalMode; compact?: boolean; shopEnabled?: boolean } = $props();
	const items = $derived(compact ? portalPhoneItems(mode, shopEnabled) : portalItems(mode, shopEnabled));
	const active = $derived(activePortalKey(page.url));
	const meActive = $derived((compact ? ME_PHONE_PAGES : ME_PAGES).includes(active));
	// The count on Me (forms and payments waiting for the family). The numbers come from the Home data, which only some pages load, so the last
	// count seen is kept (also for this browser tab) and shown on the pages that do not carry it, such as the shop.
	const fresh = $derived(mode === 'parent' && page.data.home ? (page.data.home.forms?.length ?? 0) + (page.data.home.fees ?? []).filter((fee: { balance: string }) => Number(fee.balance) > 0).length : null);
	let remembered = $state(0);
	$effect(() => {
		if (fresh !== null) {
			remembered = fresh;
			try { sessionStorage.setItem('rc-me-count', String(fresh)); } catch { /* storage blocked: the count just starts at zero */ }
		} else if (remembered === 0) {
			try { remembered = Number(sessionStorage.getItem('rc-me-count')) || 0; } catch { /* nothing remembered */ }
		}
	});
	const todo = $derived(mode === 'parent' ? (fresh ?? remembered) : 0);
</script>

<nav class={compact ? 'portal-tabs grid auto-cols-[minmax(0,1fr)] grid-flow-col' : 'flex flex-col gap-0.5 px-2.5'} aria-label={t('shell.nav.label')}>
	{#each items as item (item.href)}
		{@const selected = active === item.key || (item.key === 'me' && meActive)}
		<a class="{compact ? 'portal-tab' : 'nav-item'} {selected ? 'active' : ''}" href={item.href} aria-current={selected ? 'page' : undefined}>
			<item.icon size={compact ? 21 : 17} class="shrink-0" aria-hidden="true" />
			{t(item.label)}
			{#if compact && item.key === 'me' && todo > 0}<span class="portal-tab-badge" aria-label={t('portal.todo_count', { n: todo })}>{todo}</span>{/if}
		</a>
		{#if !compact && item.key === 'me' && meActive}
			<a class="nav-subitem {active === 'profile' ? 'active' : ''}" href="/parent/profile"><UserRound size={14} aria-hidden="true" /> {t('portal.profile')}</a>
			<a class="nav-subitem {active === 'stats' ? 'active' : ''}" href="/parent/stats"><ChartColumn size={14} aria-hidden="true" /> {t('portal.stats_page')}</a>
			<a class="nav-subitem {active === 'notifications' ? 'active' : ''}" href="/parent/notifications"><Bell size={14} aria-hidden="true" /> {t('portal.notifications')}</a>
			<a class="nav-subitem {active === 'payments' ? 'active' : ''}" href="/parent?tab=payments"><Wallet size={14} aria-hidden="true" /> {t('portal.payments')}</a>
		{/if}
	{/each}
</nav>
