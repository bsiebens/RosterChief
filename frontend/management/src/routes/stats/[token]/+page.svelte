<script lang="ts">
	import { Activity, ChartColumn } from '@lucide/svelte';
	import { page } from '$app/state';
	import { api } from '#lib/management-api.ts';
	import type { PublicPlayerStats } from './+page.server.ts';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import LangSwitcher from '#lib/components/LangSwitcher.svelte';
	import GameStatsCard from '#lib/components/members/detail/GameStatsCard.svelte';
	import GoalieStats from '#lib/components/members/stats/GoalieStats.svelte';
	import PlayerStats from '#lib/components/members/stats/PlayerStats.svelte';
	import PlayerTiles from '#lib/components/members/stats/PlayerTiles.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	/**
	 * A player's personal public stats page (no login): only the game numbers, the same cards as the "Game stats" part of
	 * the member's Stats tab, without links into the management app. The link is switched on per person by club staff.
	 */
	let { data } = $props();
	const s = $derived(data.stats);
	const club = $derived(data.mode === 'club' ? data.branding : null);
	const loadSeason = async (seasonId: string) => (await api<PublicPlayerStats>('GET', `/public/player-stats/${encodeURIComponent(page.params.token ?? '')}`, { query: { season: seasonId } })).data?.game_stats ?? null;
	const name = $derived(`${s.first_name} ${s.last_name}`);
</script>

<svelte:head>
	<title>{pageTitle(name)}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<div class="min-h-screen bg-paper font-sans text-slate">
	<header class="bg-primary" style="background-image: linear-gradient(120deg, color-mix(in srgb, var(--color-club) 55%, var(--color-primary)) 0%, var(--color-primary) 62%);">
		<div class="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-4 py-8 sm:px-6">
			<div class="min-w-0">
				<p class="font-mono text-xs tracking-[.12em] text-primary-content uppercase opacity-80">{t('publicstats.kicker')}</p>
				<h1 class="mt-1 truncate font-display text-4xl leading-none font-extrabold tracking-[.02em] text-primary-content uppercase sm:text-5xl">{name}</h1>
			</div>
			{#if club}
				<div class="flex items-center gap-3">
					<span class="font-display text-2xl leading-none font-extrabold tracking-[.06em] text-primary-content uppercase opacity-90 sm:text-3xl">{club.name}</span>
					<ClubCrest name={club.name} logoUrl={club.logo_url} size={48} />
				</div>
			{/if}
		</div>
	</header>

	<main class="mx-auto grid max-w-5xl gap-4 px-4 py-6 sm:px-6">
		{#if s.player || s.goalie}
			{#if s.player}
				<div class="grid items-stretch gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
					<GameStatsCard stats={s.game_stats} links={false} load={loadSeason} />
					<div class="card card-body gap-3">
						<h2 class="card-title"><Activity size={18} aria-hidden="true" /> {t('stats.rates')}</h2>
						<PlayerTiles player={s.player} />
					</div>
				</div>
				<PlayerStats player={s.player} links={false} />
			{:else}
				<GameStatsCard stats={s.game_stats} links={false} load={loadSeason} />
			{/if}
			{#if s.goalie}
				<h2 class="font-display text-base font-bold text-ink">{t('stats.section.goalie')}</h2>
				<GoalieStats goalie={s.goalie} />
			{/if}
		{:else}
			<div class="card card-body items-center gap-2 py-10 text-center">
				<ChartColumn size={28} class="text-dim" aria-hidden="true" />
				<p class="font-display text-lg font-bold text-ink">{t('publicstats.empty')}</p>
			</div>
		{/if}
	</main>

	<footer class="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 pb-8 sm:px-6">
		<p class="font-mono text-[11px] text-muted">{t('publicstats.footer')}</p>
		<LangSwitcher />
	</footer>
</div>
