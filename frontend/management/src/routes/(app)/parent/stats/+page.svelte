<script lang="ts">
	import { goto } from '$app/navigation';
	import { Activity, ChartColumn, Flag, ListChecks } from '@lucide/svelte';
	import PersonFilter from '#lib/components/PersonFilter.svelte';
	import GameStatsCard from '#lib/components/members/detail/GameStatsCard.svelte';
	import AttendanceStats from '#lib/components/members/stats/AttendanceStats.svelte';
	import GoalieStats from '#lib/components/members/stats/GoalieStats.svelte';
	import OfficiatingStats from '#lib/components/members/stats/OfficiatingStats.svelte';
	import PlayerStats from '#lib/components/members/stats/PlayerStats.svelte';
	import PlayerTiles from '#lib/components/members/stats/PlayerTiles.svelte';
	import StatsShare from '#lib/components/portal/StatsShare.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import type { MyStats } from './+page.server.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: t('portal.stats_page') }));

	// svelte-ignore state_referenced_locally
	let who = $state(data.who);
	$effect(() => {
		who = data.who;
	});
	const person = $derived(data.people.find((candidate) => candidate.id === data.who));
	const s = $derived(data.stats);
	$effect(() => {
		if (who && who !== data.who) void goto(`/parent/stats?who=${who}`, { replaceState: true });
	});
	const loadSeason = async (seasonId: string) => (await api<MyStats>('GET', `/portal/me/stats/${data.who}`, { query: { season: seasonId } })).data?.game_stats ?? null;
	const referee = $derived(s?.officiating?.referee ?? null);
	const official = $derived(s?.officiating?.official ?? null);
	const hasGames = $derived(!!s?.game_stats && !!(s.player || s.goalie));
	const empty = $derived(!hasGames && !s?.attendance && !referee && !official);
</script>

<svelte:head><title>{pageTitle(t('portal.stats_page'))}</title></svelte:head>

{#snippet heading(label: string, Icon: typeof Activity)}
	<h2 class="flex items-center gap-2 font-display text-sm font-bold tracking-[.12em] text-muted uppercase"><Icon size={16} aria-hidden="true" /> {label}</h2>
{/snippet}

<div class="grid min-w-0 gap-4">
	<PersonFilter people={data.people} bind:selected={who} all={false} viewer={data.people.find((p) => p.is_self)?.id ?? ''} />
	{#if data.available && person}<StatsShare {person} banner />{/if}
	{#if empty}
		<div class="card card-body items-center gap-2 py-10 text-center"><ChartColumn size={28} class="text-dim" aria-hidden="true" /><p class="font-display text-lg font-bold text-ink">{t('publicstats.empty')}</p></div>
	{/if}
	{#if s && hasGames && s.game_stats}
		<section class="grid gap-3">
			{@render heading(t('stats.section.games'), Activity)}
			{#if s.player}
				<div class="grid items-stretch gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
					<GameStatsCard stats={s.game_stats} links={false} load={loadSeason} />
					<div class="card card-body gap-3">
						<h3 class="card-title"><Activity size={18} aria-hidden="true" /> {t('stats.rates')}</h3>
						<PlayerTiles player={s.player} />
					</div>
				</div>
				<PlayerStats player={s.player} links={false} />
			{:else}
				<GameStatsCard stats={s.game_stats} links={false} load={loadSeason} />
			{/if}
			{#if s.goalie}
				<h3 class="font-display text-base font-bold text-ink">{t('stats.section.goalie')}</h3>
				<GoalieStats goalie={s.goalie} />
			{/if}
		</section>
	{/if}
	{#if s?.attendance}
		<section class="grid gap-3">
			{@render heading(t('stats.section.attendance'), ListChecks)}
			<AttendanceStats attendance={s.attendance} />
		</section>
	{/if}
	{#if referee || official}
		<section class="grid gap-3">
			{@render heading(t('stats.section.officiating'), Flag)}
			<div class="grid gap-4">
				{#if referee}<OfficiatingStats kind="referee" stats={referee} />{/if}
				{#if official}<OfficiatingStats kind="official" stats={official} />{/if}
			</div>
		</section>
	{/if}
</div>
