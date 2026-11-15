<script lang="ts">
	import { afterNavigate } from '$app/navigation';
	import { Menu } from '@lucide/svelte';
	import { header } from '#lib/header.svelte.ts';
	import AppSwitcher from '#lib/components/AppSwitcher.svelte';
	import PortalNav from '#lib/components/PortalNav.svelte';
	import PortalRail from '#lib/components/PortalRail.svelte';
	import { rememberApp } from '#lib/landing.ts';
	import Sidebar from '#lib/components/Sidebar.svelte';
	import AccountMenu from '#lib/components/AccountMenu.svelte';
	import ReauthDialog from '#lib/components/ReauthDialog.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import NotificationBell from '#lib/components/NotificationBell.svelte';
	import LangSwitcher from '#lib/components/LangSwitcher.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	import { page } from '$app/state';

	let { data, children } = $props();

	const a = $derived(data.auth);
	const email = $derived(a.status === 'anonymous' || a.status === 'mfa_pending' ? '' : a.user.email);
	// The full club shell needs the /me payload; pickers, no-access and MFA enrolment get the plain top bar only.
	const club = $derived(data.mode === 'club' && a.status === 'authenticated' ? data.branding : null);
	const parent = $derived(page.url.pathname === '/parent' || page.url.pathname.startsWith('/parent/'));
	const coach = $derived(page.url.pathname === '/coach' || page.url.pathname.startsWith('/coach/') || page.url.searchParams.get('view') === 'coach');
	const portal = $derived(parent ? 'parent' : coach ? 'coach' : null);
	const clubShell = $derived(club && (data.me || portal));
	const management = $derived(clubShell && !portal);
	const home = $derived(parent ? '/parent' : coach ? '/coach' : '/');
	const season = $derived(parent ? page.data.home?.season : page.data.coach?.season);
	const shopEnabled = $derived(parent && (data.me?.features?.includes('shop') || (a.status === 'authenticated' && a.memberShopEnabled === true)));
	let navOpen = $state(false);
	afterNavigate(() => { navOpen = false; });
	// People with both a member side and a staff side land in the app they used last (see lib/landing.ts).
	$effect(() => {
		if (!data.me?.has_member_portal_role) return;
		const path = page.url.pathname;
		if (parent) rememberApp('member');
		else if (!path.startsWith('/account') && path !== '/no-access') rememberApp('manage');
	});
</script>

<div class="flex min-h-screen bg-paper font-sans text-slate" class:portal-ui={!!portal} class:portal-coach={coach}>
	{#if clubShell && club}
		{#if portal}
			<div class="hidden xl:block"><Sidebar me={data.me} branding={club} {email} {portal} {shopEnabled} /></div>
			<div class="hidden md:block xl:hidden"><PortalRail me={data.me} branding={club} mode={portal} {shopEnabled} /></div>
		{:else}
			<div class="hidden lg:block"><Sidebar me={data.me} branding={club} {email} {portal} {shopEnabled} /></div>
		{/if}
	{/if}

	<div class="flex min-w-0 flex-1 flex-col">
		{#if clubShell && club}
			<div class="sticky top-0 z-30 bg-sidebar-bg text-sidebar-fg {portal ? 'md:hidden' : 'lg:hidden'}" class:portal-masthead={!!portal}>
				<div class="flex items-start justify-between gap-2 px-3 pt-3 pb-2 sm:px-5">
					<div class="grid min-w-0 content-start gap-2">
						<a class="flex min-w-0 items-center gap-2.5" href={home}>
							<ClubCrest name={club.name} logoUrl={club.logo_url} size={30} />
							<span class="min-w-0">
								<span class="block truncate font-display text-lg leading-tight font-extrabold tracking-[.06em] uppercase">{club.name}</span>
								{#if portal && (coach || season)}
									<span class="portal-mode-label flex flex-wrap items-center gap-x-2 text-xs">
										{#if coach}<strong class="font-display tracking-wider uppercase">{t('portal.coach')}</strong>{/if}
										{#if season}<span>{season}</span>{/if}
									</span>
								{/if}
							</span>
						</a>
					</div>
					<div class="flex shrink-0 items-center gap-1">
						{#if management}
							<details class="relative" bind:open={navOpen}>
								<summary class="grid size-10 cursor-pointer list-none place-items-center rounded hover:bg-sidebar-chip [&::-webkit-details-marker]:hidden" aria-label={t('shell.nav.label')}>
									<Menu size={20} aria-hidden="true" />
								</summary>
								{#if navOpen}
									<div class="absolute right-0 z-20 mt-1 overflow-hidden rounded-md border border-sidebar-hairline shadow-lg" onclick={(event) => { if ((event.target as HTMLElement).closest('a')) navOpen = false; }} role="presentation">
										<Sidebar me={data.me} branding={club} {email} compact />
									</div>
								{/if}
							</details>
						{/if}
						{#if portal}<NotificationBell dark={coach} portal={portal} />{/if}
						{#if !portal}<AccountMenu {email} dark />{/if}
					</div>
				</div>
				{#if data.me?.has_member_portal_role}<div class="px-3 pb-2 sm:px-5"><AppSwitcher member={parent} tone={parent ? 'light' : 'sidebar'} /></div>{/if}
			</div>
		{/if}

		<header class="z-10 min-h-14 shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-line bg-white px-3 py-2 sm:px-5 {portal ? 'md:sticky md:top-0 md:min-h-16 md:gap-4 md:px-7' : 'lg:sticky lg:top-0 lg:min-h-16 lg:gap-4 lg:px-7'} {portal && page.url.pathname === home ? 'hidden md:flex' : 'flex'}" class:portal-topbar={!!portal}>
			{#if !clubShell}
				<a class="flex shrink-0 items-center gap-2.5" href={home}>
					{#if data.mode === 'club' && data.branding}
						<ClubCrest name={data.branding.name} logoUrl={data.branding.logo_url} size={30} />
						<span class="hidden font-display text-lg font-extrabold tracking-[.08em] text-ink uppercase md:inline">{data.branding.name}</span>
					{:else}
						<img class="h-7 w-7 shrink-0" src="/images/rosterchief-dark.svg" alt="" width="28" height="28" />
						<span class="font-display text-lg font-extrabold tracking-[.1em] text-ink uppercase">RosterChief</span>
					{/if}
				</a>
				<span class="h-6 w-px bg-line" aria-hidden="true"></span>
			{/if}
			<span class="min-w-0 truncate font-display text-lg font-extrabold text-ink uppercase sm:text-2xl">{header.title}</span>
			{#if parent && season}<span class="badge badge-outline hidden md:inline-flex">{season}</span>{/if}
			{#if header.context}<span class="hidden font-mono text-xs text-muted sm:inline">{header.context}</span>{/if}
			<!-- The badge and buttons are snippets owned by the page: while navigating away they can briefly read the next page's data
			     and throw. The boundary swallows that (the next page sets its own header a moment later). -->
			<svelte:boundary>
				{#if header.badge}{@render header.badge()}{/if}
				{#snippet failed()}{/snippet}
			</svelte:boundary>
			<div class="flex-1"></div>
			<svelte:boundary>
				{#if header.actions}
					<div class="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2">{@render header.actions()}</div>
				{/if}
				{#snippet failed()}{/snippet}
			</svelte:boundary>
			{#if management}<NotificationBell />{/if}
			{#if portal}<div class="hidden md:block"><NotificationBell {portal} /></div>{/if}
			{#if !clubShell}<LangSwitcher /><AccountMenu {email} />{/if}
		</header>

		<main class="flex-1">
			<div class="flex min-w-0 flex-col gap-4 px-3 py-4 sm:px-5 sm:py-5 {portal ? 'md:px-7 md:py-6' : 'lg:px-7 lg:py-6'}">
				<!-- Keyed by path: on navigation the old page is torn down before it can be handed the next page's data (that
				     briefly happens otherwise and crashes pages that read fields only their own data has: the 500 on going back). -->
				{#key page.url.pathname}
					{@render children()}
				{/key}
			</div>
		</main>
		{#if portal}
			<div class="sticky bottom-0 z-20 md:hidden">
				<PortalNav mode={portal} compact {shopEnabled} />
			</div>
		{/if}
	</div>
</div>

<ReauthDialog />
