<script lang="ts">
	import { page } from '$app/state';
	import PublicShell from '#lib/components/registration/PublicShell.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/** The frame of one tournament's public pages: the club header with the tournament name and the section tabs. */
	let { data, children } = $props();
	const tn = $derived(data.tournament);
	const club = $derived(data.mode === 'club' ? data.branding : null);
	const base = $derived(`/tournaments/${tn.path}`);
	const tabs = $derived([
		{ href: base, label: t('tp.nav.overview'), exact: true },
		{ href: `${base}/schedule`, label: t('tp.nav.schedule') },
		{ href: `${base}/standings`, label: t('tp.nav.standings') },
		{ href: `${base}/teams`, label: t('tp.nav.teams') },
		...(tn.accepts_registrations ? [{ href: `${base}/register`, label: t('tp.nav.register') }] : [])
	]);
	const current = (tab: { href: string; exact?: boolean }) => (tab.exact ? page.url.pathname === tab.href : page.url.pathname.startsWith(tab.href));
</script>

<PublicShell title={tn.name} kicker={t('tp.kicker')} {club} wide>
	<nav class="flex flex-wrap gap-2" aria-label={tn.name}>
		{#each tabs as tab (tab.href)}
			<a
				href={tab.href}
				aria-current={current(tab) ? 'page' : undefined}
				class="rounded-md px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {current(tab) ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}">{tab.label}</a
			>
		{/each}
	</nav>
	{@render children()}
</PublicShell>
