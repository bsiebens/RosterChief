<script lang="ts">
	import { CalendarDays, Copyright, MapPin } from '@lucide/svelte';
	import { page } from '$app/state';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import LangSwitcher from '#lib/components/LangSwitcher.svelte';
	import AlertsButton from '#lib/components/tournaments/public/AlertsButton.svelte';
	import LiveBadge from '#lib/components/tournaments/LiveBadge.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { range } from '#lib/tournaments-public.ts';

	/** The frame of one tournament's public pages: a hero with the facts, a sticky tab bar, and a wide content area. */
	let { data, children } = $props();
	const tn = $derived(data.tournament);
	const club = $derived(data.mode === 'club' ? data.branding : null);
	const base = $derived(`/tournaments/${tn.path}`);
	const live = $derived(tn.live_matches.length);
	const tabs = $derived([
		{ href: base, label: t('tp.nav.overview'), exact: true },
		{ href: `${base}/schedule`, label: t('tp.nav.schedule') },
		{ href: `${base}/standings`, label: t('tp.nav.standings') },
		{ href: `${base}/teams`, label: t('tp.nav.teams') },
		...(tn.accepts_registrations ? [{ href: `${base}/register`, label: t('tp.nav.register'), cta: true }] : [])
	]);
	const current = (tab: { href: string; exact?: boolean }) => (tab.exact ? page.url.pathname === tab.href : page.url.pathname.startsWith(tab.href));
	// the big hero is for the overview; the other pages keep a slim one so the content starts higher
	const slim = $derived(page.url.pathname !== base);
</script>

<div class="min-h-screen bg-paper font-sans text-slate">
	<header class="relative overflow-hidden bg-primary text-primary-content" style="background-image: radial-gradient(60rem 28rem at 85% -20%, color-mix(in srgb, var(--color-club) 70%, transparent), transparent), linear-gradient(120deg, color-mix(in srgb, var(--color-club) 40%, var(--color-primary)), var(--color-primary) 70%);">
		<div class="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 pt-4 sm:px-6">
			<a href="/tournaments" class="flex items-center gap-2.5 opacity-90 hover:opacity-100">
				{#if club}<ClubCrest name={club.name} logoUrl={club.logo_url} size={32} />{/if}
				<span class="font-display text-sm font-extrabold tracking-[.1em] uppercase">{club?.name ?? t('tp.kicker')}</span>
			</a>
			<div class="flex items-center gap-3">{#if tn.status === 'published' || tn.status === 'in_progress'}<AlertsButton slug={tn.slug} />{/if}<LangSwitcher /></div>
		</div>
		<div class="mx-auto flex max-w-7xl flex-wrap items-end justify-between gap-x-8 gap-y-4 px-4 sm:px-6 {slim ? 'py-5' : 'py-10 sm:py-14'}">
			<div class="min-w-0">
				<div class="flex flex-wrap items-center gap-2">
					<span class="rounded-full bg-white/15 px-3 py-0.5 font-mono text-[11px] tracking-[.12em] uppercase">{t(`tp.status.${tn.status}`)}</span>
					{#if live}<span class="rounded-full bg-white px-1.5 py-0.5"><LiveBadge /></span>{/if}
				</div>
				<h1 class="mt-3 font-display leading-[1.05] font-extrabold tracking-[.01em] uppercase break-words {slim ? 'text-3xl' : 'text-4xl sm:text-6xl'}">{tn.name}</h1>
				<p class="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm opacity-90 sm:text-base">
					<span class="flex items-center gap-2"><CalendarDays size={17} aria-hidden="true" />{range(tn.start_date, tn.end_date)}</span>
					{#if tn.location}<span class="flex items-center gap-2"><MapPin size={17} aria-hidden="true" />{tn.location}{#if tn.location_address}<span class="hidden opacity-75 sm:inline">· {tn.location_address}</span>{/if}</span>{/if}
				</p>
			</div>
			{#if tn.accepts_registrations && !slim}
				<a class="btn btn-lg border-0 bg-white font-display tracking-[.08em] text-primary uppercase shadow-lg hover:bg-white/90" href="{base}/register">{t('tp.register_cta')}</a>
			{/if}
		</div>
	</header>

	<nav class="sticky top-0 z-20 border-b border-line bg-white/95 backdrop-blur" aria-label={tn.name}>
		<div class="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-3 sm:px-5">
			{#each tabs as tab (tab.href)}
				<a
					href={tab.href}
					aria-current={current(tab) ? 'page' : undefined}
					class="-mb-px shrink-0 border-b-[3px] px-4 py-3.5 font-display text-sm font-extrabold tracking-[.1em] whitespace-nowrap uppercase {current(tab) ? 'border-club text-ink' : 'border-transparent text-muted hover:text-ink'} {tab.cta ? 'ml-auto text-club' : ''}">{tab.label}</a
				>
			{/each}
		</div>
	</nav>

	<main class="mx-auto grid max-w-7xl gap-5 px-4 py-6 sm:px-6 sm:py-8">{@render children()}</main>

	<footer class="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 pb-8 sm:px-6">
		<p class="flex items-center gap-1 font-mono text-[11px] text-muted"><Copyright size={12} aria-hidden="true" /> RosterChief {new Date().getFullYear()}</p>
		{#if tn.contact_email}<a class="font-mono text-[11px] text-muted hover:text-ink" href="mailto:{tn.contact_email}">{tn.contact_email}</a>{/if}
	</footer>
</div>
