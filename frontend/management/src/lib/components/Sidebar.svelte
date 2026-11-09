<script lang="ts">
	import { page } from '$app/state';
	import { ChevronUp, KeyRound, LogOut, ShieldCheck } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { visibleNav, isActive } from '#lib/nav.ts';
	import { signOut } from '#lib/session.ts';
	import type { Branding, Me } from '#lib/types.ts';
	import ClubCrest from './ClubCrest.svelte';
	import LangSwitcher from './LangSwitcher.svelte';

	let { me, branding }: { me: Me; branding: Branding } = $props();

	const items = $derived(visibleNav(me));
	const path = $derived(page.url.pathname);
	// Counts behind the nav badges (not-yet-ready sign-ups), for people who manage members. Refreshed whenever the page changes.
	let counts = $state<Record<string, number>>({});
	$effect(() => {
		void path;
		if (!me.can_manage_members) return;
		fetch('/api/v1/signups/summary', { headers: { accept: 'application/json' } })
			.then((r) => (r.ok ? r.json() : null))
			.then((j) => j && (counts.signups = j.not_clean ?? 0))
			.catch(() => {});
	});
	const fullName = $derived(`${me.first_name} ${me.last_name}`.trim() || me.email);
	const initials = $derived(((me.first_name[0] ?? '') + (me.last_name[0] ?? '')).toUpperCase() || me.email[0]?.toUpperCase() || '?');

	let menu: HTMLDetailsElement | undefined = $state();
	const close = () => menu && (menu.open = false);
</script>

<svelte:window onclick={(e) => menu?.open && !menu.contains(e.target as Node) && close()} onkeydown={(e) => e.key === 'Escape' && close()} />

<aside class="sticky top-0 flex h-screen w-[236px] shrink-0 flex-col overflow-y-auto bg-sidebar-bg py-5">
	<a class="flex items-center gap-2.5 px-[18px] pb-[22px]" href="/">
		<ClubCrest name={branding.name} logoUrl={branding.logo_url} size={30} />
		<span class="min-w-0">
			<span class="block truncate font-display text-[17px] leading-none font-extrabold tracking-[.06em] text-sidebar-fg uppercase">{branding.name}</span>
			<span class="block font-mono text-[10px] text-sidebar-fg-faint">RosterChief &middot; management</span>
		</span>
	</a>

	<nav class="flex flex-col gap-0.5 px-2.5" aria-label={t('shell.nav.label')}>
		{#each items as item (item.key)}
			{@const active = isActive(item, path)}
			<div>
				<a class="nav-item {active ? 'active' : ''}" href={item.href} aria-current={active ? 'page' : undefined}>
					<item.icon size={17} aria-hidden="true" />
					{t(item.label)}
					{#if item.badge && counts[item.badge]}<span class="ml-auto rounded-full bg-sidebar-chip px-1.5 font-mono text-[10px] leading-4 font-bold text-sidebar-fg" aria-label={t('signups.badge', { n: counts[item.badge] })}>{counts[item.badge]}</span>{/if}
				</a>
				{#if active && item.children?.length}
					<div class="flex flex-col">
						{#each item.children as child (child.href)}
							{@const childActive = child.match ? child.match(path) : path === child.href}
							<a class="nav-subitem {childActive ? 'active' : ''}" href={child.href} aria-current={childActive ? 'page' : undefined}>{t(child.label)}{#if child.badge && counts[child.badge]}<span class="ml-2 rounded-full bg-sidebar-chip px-1.5 font-mono text-[10px] leading-4 font-bold text-sidebar-fg" aria-label={t('signups.badge', { n: counts[child.badge] })}>{counts[child.badge]}</span>{/if}</a>
						{/each}
					</div>
				{/if}
			</div>
		{/each}
	</nav>

	<div class="flex-1"></div>

	<div class="px-[18px]">
		<details class="relative border-t border-sidebar-hairline pt-3.5" bind:this={menu}>
			<summary class="flex cursor-pointer list-none items-center gap-2.5 [&::-webkit-details-marker]:hidden">
				<span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sidebar-chip font-display text-[13px] font-extrabold text-sidebar-fg">{initials}</span>
				<span class="min-w-0 flex-1">
					<span class="block truncate text-[13px] font-semibold text-sidebar-fg">{fullName}</span>
					<span class="block text-[11px] text-sidebar-fg-faint">{me.is_club_admin ? t('shell.role.admin') : t('shell.role.staff')}</span>
				</span>
				<ChevronUp size={14} class="shrink-0 text-sidebar-fg-faint" aria-hidden="true" />
			</summary>
			<div class="absolute bottom-full left-0 z-20 mb-2 w-full rounded-xl border border-sidebar-hairline bg-sidebar-bg p-1.5 shadow-lg">
				<a class="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] font-medium text-sidebar-fg-dim hover:bg-sidebar-hairline hover:text-sidebar-fg" href="/account/password" onclick={close}>
					<KeyRound size={15} aria-hidden="true" />
					{t('shell.menu.password')}
				</a>
				<a class="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] font-medium text-sidebar-fg-dim hover:bg-sidebar-hairline hover:text-sidebar-fg" href="/account/security" onclick={close}>
					<ShieldCheck size={15} aria-hidden="true" />
					{t('shell.menu.security')}
				</a>
				<div class="my-1 h-px bg-sidebar-hairline"></div>
				<div class="flex items-center justify-between px-2.5 py-1.5">
					<span class="text-[11px] text-sidebar-fg-faint">{t('shell.menu.language')}</span>
					<LangSwitcher tone="sidebar" />
				</div>
				<div class="my-1 h-px bg-sidebar-hairline"></div>
				<button class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] font-medium text-sidebar-fg-dim hover:bg-sidebar-hairline hover:text-sidebar-fg" onclick={signOut}>
					<LogOut size={15} aria-hidden="true" />
					{t('shell.menu.logout')}
				</button>
			</div>
		</details>
	</div>
</aside>
