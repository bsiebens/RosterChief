<script lang="ts">
	import { Activity, BarChart3, LoaderCircle } from '@lucide/svelte';
	import { invalidateAll } from '$app/navigation';
	import { onMount } from 'svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { MemberGameStats } from '#lib/analytics.ts';
	import type { Officiating } from '#lib/members/eligibility.ts';
	import { hasStats, type MemberStats } from '#lib/member-stats.ts';
	import GameStatsCard from '#lib/components/members/detail/GameStatsCard.svelte';
	import StatsShare from '#lib/components/members/detail/StatsShare.svelte';
	import AttendanceStats from '#lib/components/members/stats/AttendanceStats.svelte';
	import GoalieStats from '#lib/components/members/stats/GoalieStats.svelte';
	import OfficiatingStats from '#lib/components/members/stats/OfficiatingStats.svelte';
	import PlayerStats from '#lib/components/members/stats/PlayerStats.svelte';
	import PlayerTiles from '#lib/components/members/stats/PlayerTiles.svelte';
	import StaffStats from '#lib/components/members/stats/StaffStats.svelte';

	/**
	 * The member's Stats tab. Sections only appear when they apply: player and goalie from the game sheets, turnout from
	 * attendance, staff history from the team staff, officiating for referees and officials.
	 */
	let { stats, gameStats, officiating, memberId, canShare = false }: { stats: MemberStats | null; gameStats: MemberGameStats | null; officiating: Officiating | null; memberId: string; canShare?: boolean } = $props();
	const updating = $derived(!!stats?.update_in_progress);
	// While the background re-link runs, look again every few seconds; the banner and the numbers update when it is done.
	onMount(() => {
		const timer = setInterval(() => {
			if (updating) invalidateAll();
		}, 3000);
		return () => clearInterval(timer);
	});
	/** The game-stats card's season picker: that season's numbers, or null when they could not be loaded. */
	const loadSeason = async (seasonId: string) => (await api<MemberGameStats>('GET', `/members/${memberId}/game-stats`, { query: { season: seasonId } })).data ?? null;
	const referee = $derived(officiating?.referee ?? null);
	const official = $derived(officiating?.official ?? null);
</script>

{#snippet heading(label: string)}
	<h2 class="font-display text-sm font-bold tracking-[.12em] text-muted uppercase">{label}</h2>
{/snippet}

{#if updating}
	<div class="mb-4"><Alert kind="info" title={t('stats.updating.title')}>
		<span class="flex items-start gap-2"><LoaderCircle size={16} class="mt-0.5 shrink-0 animate-spin" aria-hidden="true" />{t('stats.updating.body')}</span>
	</Alert></div>
{/if}

{#if !hasStats(stats) && !referee && !official}
	<div class="card card-body items-center gap-2 py-10 text-center">
		<BarChart3 size={28} class="text-dim" aria-hidden="true" />
		<p class="font-display text-lg font-bold text-ink">{t('stats.empty.title')}</p>
		<p class="text-sm text-muted">{t('stats.empty.body')}</p>
	</div>
{:else}
	<div class="grid gap-6">
		{#if stats?.player || stats?.goalie}
			<section class="grid gap-3">
				{@render heading(t('stats.section.games'))}
				{#if canShare && stats?.analytics_available}<StatsShare {memberId} token={stats.share_token ?? null} />{/if}
				{#if gameStats && stats?.player}
					<!-- The totals take two thirds, the four headline rates sit in a smaller box beside them. -->
					<div class="grid items-stretch gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
						<GameStatsCard stats={gameStats} load={loadSeason} />
						<div class="card card-body gap-3">
							<h2 class="card-title"><Activity size={18} aria-hidden="true" /> {t('stats.rates')}</h2>
							<PlayerTiles player={stats.player} />
						</div>
					</div>
				{:else if gameStats}
					<GameStatsCard stats={gameStats} load={loadSeason} />
				{/if}
				{#if stats?.player}<PlayerStats player={stats.player} />{/if}
				{#if stats?.goalie}
					<h3 class="font-display text-base font-bold text-ink">{t('stats.section.goalie')}</h3>
					<GoalieStats goalie={stats.goalie} />
				{/if}
			</section>
		{/if}
		{#if stats?.attendance}
			<section class="grid gap-3">
				{@render heading(t('stats.section.attendance'))}
				<AttendanceStats attendance={stats.attendance} />
			</section>
		{/if}
		{#if stats && stats.staff.length}
			<section class="grid gap-3">
				{@render heading(t('stats.section.staff'))}
				<StaffStats staff={stats.staff} canOpenAnalytics={stats.analytics_available} />
			</section>
		{/if}
		{#if referee || official}
			<section class="grid gap-3">
				{@render heading(t('stats.section.officiating'))}
				<div class="grid gap-4">
					{#if referee}<OfficiatingStats kind="referee" profile={referee} stats={stats?.officiating?.referee ?? null} />{/if}
					{#if official}<OfficiatingStats kind="official" profile={official} stats={stats?.officiating?.official ?? null} />{/if}
				</div>
			</section>
		{/if}
	</div>
{/if}
