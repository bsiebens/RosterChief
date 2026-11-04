<script lang="ts">
	import { header } from '#lib/header.svelte.ts';
	import Sidebar from '#lib/components/Sidebar.svelte';
	import AccountMenu from '#lib/components/AccountMenu.svelte';
	import ReauthDialog from '#lib/components/ReauthDialog.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import NotificationBell from '#lib/components/NotificationBell.svelte';

	let { data, children } = $props();

	const a = $derived(data.auth);
	const email = $derived(a.status === 'anonymous' || a.status === 'mfa_pending' ? '' : a.user.email);
	// The full club shell needs the /me payload; pickers, no-access and MFA enrolment get the plain top bar only.
	const club = $derived(data.mode === 'club' && a.status === 'authenticated' ? data.branding : null);
</script>

<!-- Club panel shell (old base.html): sticky 236px sidebar + sticky 64px topbar, the whole page scrolls. -->
<div class="flex min-h-screen bg-paper font-sans text-slate">
	{#if club}
		<Sidebar me={data.me} branding={club} />
	{/if}

	<div class="flex min-w-0 flex-1 flex-col">
		<header class="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-4 border-b border-line bg-white px-7">
			{#if !club}
				<a class="flex shrink-0 items-center gap-2.5" href="/">
					{#if data.mode === 'club' && data.branding}
						<ClubCrest name={data.branding.name} logoUrl={data.branding.logo_url} size={30} />
						<span class="font-display text-lg font-extrabold tracking-[.08em] text-ink uppercase">{data.branding.name}</span>
					{:else}
						<img class="h-7 w-7 shrink-0" src="/images/rosterchief-dark.svg" alt="" width="28" height="28" />
						<span class="font-display text-lg font-extrabold tracking-[.1em] text-ink uppercase">RosterChief</span>
					{/if}
				</a>
				<span class="h-6 w-px bg-line" aria-hidden="true"></span>
			{/if}
			<span class="truncate font-display text-2xl font-extrabold text-ink uppercase">{header.title}</span>
			{#if header.context}<span class="font-mono text-xs text-muted">{header.context}</span>{/if}
			{#if header.badge}{@render header.badge()}{/if}
			<div class="flex-1"></div>
			{#if header.actions}
				<div class="flex items-center gap-2">{@render header.actions()}</div>
			{/if}
			{#if club}<NotificationBell />{:else}<AccountMenu {email} />{/if}
		</header>

		<main class="flex-1">
			<div class="flex flex-col gap-4 px-7 py-6">
				{@render children()}
			</div>
		</main>
	</div>
</div>

<ReauthDialog />
