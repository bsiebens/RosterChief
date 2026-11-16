<script lang="ts">
	import { page } from '$app/state';
	import { CalendarDays, ChevronUp, KeyRound, LogOut, ShieldCheck } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { visibleNav, isActive } from '#lib/nav.ts';
	import { signOut } from '#lib/session.ts';
	import type { Branding, Me } from '#lib/types.ts';
	import ClubCrest from './ClubCrest.svelte';
	import LangSwitcher from './LangSwitcher.svelte';
	import AppSwitcher from './AppSwitcher.svelte';
	import PortalNav from './PortalNav.svelte';

	let { me, branding, email, portal = null, compact = false, shopEnabled = false }: {
		me: Me | null; branding: Branding; email: string; portal?: 'parent' | 'coach' | null; compact?: boolean; shopEnabled?: boolean;
	} = $props();

	const items = $derived(me && !portal ? visibleNav(me) : []);
	const path = $derived(page.url.pathname);
	// Counts behind the nav badges (not-yet-ready sign-ups and registrations awaiting review), for people who manage members. Refreshed whenever the page changes.
	let counts = $state<Record<string, number>>({});
	$effect(() => {
		void path;
		const get = (url: string) => fetch(url, { headers: { accept: 'application/json' } }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
		// Sign-ups not ready to approve (members managers), and registrations waiting for their invoice to be confirmed (finance).
		if (!me || portal) return;
		if (me.can_manage_members)
			get('/api/v1/signups/summary').then((s) => {
				counts.signups = s?.not_clean ?? 0;
				counts.members = counts.signups;
			});
		// Finance: registrations waiting for review plus invoices with something still open; the Finance entry shows both added up.
		if (me.can_manage_finance)
			Promise.all([get('/api/v1/registrations?status=awaiting&limit=1'), get('/api/v1/registrations?status=confirmed&payment=open&limit=1'), get('/api/v1/finance/open-members?limit=1')]).then(([a, o, m]) => {
				counts.registrations = a?.awaiting_count ?? 0;
				counts.payments = (o?.count ?? 0) + (m?.count ?? 0);
				counts.finance = counts.registrations + counts.payments;
			});
	});
	const badgeLabel = (badge: string, n: number) => t(badge === 'registrations' ? 'reg.badge' : badge === 'payments' ? 'fin.badge' : badge === 'finance' ? 'fin.badge_all' : 'signups.badge', { n });
	const fullName = $derived(me ? `${me.first_name} ${me.last_name}`.trim() || me.email : email);
	const initials = $derived(((me?.first_name[0] ?? '') + (me?.last_name[0] ?? '')).toUpperCase() || email[0]?.toUpperCase() || '?');
	const home = $derived(portal === 'parent' ? '/parent' : portal === 'coach' ? '/coach' : '/');

	let menu: HTMLDetailsElement | undefined = $state();
	const close = () => menu && (menu.open = false);
</script>

<svelte:window onclick={(e) => menu?.open && !menu.contains(e.target as Node) && close()} onkeydown={(e) => e.key === 'Escape' && close()} />

<aside class="flex w-[220px] shrink-0 flex-col overflow-y-auto bg-sidebar-bg {compact ? 'max-h-[65dvh] py-2' : 'sticky top-0 h-dvh py-5'}">
	{#if !compact}
		<a class="flex items-center gap-2.5 px-[18px] pb-4" href={home}>
			<ClubCrest name={branding.name} logoUrl={branding.logo_url} size={30} />
			<span class="min-w-0">
				<span class="block truncate font-display text-[17px] leading-none font-extrabold tracking-[.06em] text-sidebar-fg uppercase">{branding.name}</span>
				<span class="block font-mono text-[10px] text-sidebar-fg-dim">RosterChief{#if __APP_VERSION__}&nbsp;v{__APP_VERSION__}{/if}{#if !me} &middot; {t('portal.parent')}{/if}</span>
			</span>
		</a>
		{#if me?.has_member_portal_role}<div class="px-[18px] pb-4"><AppSwitcher member={portal === 'parent'} /></div>{/if}
	{/if}

	{#if portal}
		<PortalNav mode={portal} {shopEnabled} />
	{:else}
		<nav class="flex flex-col gap-0.5 px-2.5" aria-label={t('shell.nav.label')}>
			{#if compact && me?.member_id && !me.has_member_portal_role}
				<a class="nav-item" href="/coach"><CalendarDays size={17} aria-hidden="true" />{t('portal.coach')}</a>
			{/if}
			{#each items as item (item.key)}
				{@const active = isActive(item, path)}
				<div>
					<a class="nav-item {active ? 'active' : ''}" href={item.href} aria-current={active ? 'page' : undefined}>
						<item.icon size={17} aria-hidden="true" />
						{t(item.label)}
						{#if item.badge && counts[item.badge]}<span class="ml-auto rounded-full bg-sidebar-chip px-1.5 font-mono text-[10px] leading-4 font-bold text-sidebar-fg" aria-label={badgeLabel(item.badge, counts[item.badge])}>{counts[item.badge]}</span>{/if}
					</a>
					{#if active && item.children?.length}
						<div class="flex flex-col">
							{#each item.children as child (child.href)}
								{@const childActive = child.match ? child.match(path) : path === child.href}
								<a class="nav-subitem {childActive ? 'active' : ''}" href={child.href} aria-current={childActive ? 'page' : undefined}>{t(child.label)}{#if child.badge && counts[child.badge]}<span class="ml-2 rounded-full bg-sidebar-chip px-1.5 font-mono text-[10px] leading-4 font-bold text-sidebar-fg" aria-label={badgeLabel(child.badge, counts[child.badge])}>{counts[child.badge]}</span>{/if}</a>
							{/each}
						</div>
					{/if}
				</div>
			{/each}
		</nav>
	{/if}

	{#if !compact}
		<div class="flex-1"></div>

		<div class="px-[18px]">
			<details class="relative border-t border-sidebar-hairline pt-3.5" bind:this={menu}>
				<summary class="flex cursor-pointer list-none items-center gap-2.5 [&::-webkit-details-marker]:hidden">
					<span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sidebar-chip font-display text-[13px] font-extrabold text-sidebar-fg">{initials}</span>
					<span class="min-w-0 flex-1">
						<span class="block truncate text-[13px] font-semibold text-sidebar-fg">{fullName}</span>
						<span class="block text-[11px] text-sidebar-fg-dim">{t(!me ? 'portal.parent' : me.is_club_admin ? 'shell.role.admin' : 'shell.role.staff')}</span>
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
	{/if}
</aside>
