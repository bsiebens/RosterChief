<script lang="ts">
	import { Whistle, CalendarDays, CalendarRange, Trophy } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { monthLabel, type OfficiatingKind } from '#lib/member-stats.ts';
	import type { EligibilityProfile } from '#lib/members/eligibility.ts';
	import BarChart from './BarChart.svelte';
	import HBars from './HBars.svelte';
	import Tile from './Tile.svelte';

	/**
	 * Referee / official numbers from the real game assignments (past games only): games this season, per month split by
	 * type, per season, fees this season, penalties per game (from the recorded game timelines) and the games per
	 * competition bucket ("{federation} {team}"). The level and validity come from the profile.
	 */
	let { kind, profile = null, stats }: { kind: 'referee' | 'official'; profile?: EligibilityProfile | null; stats: OfficiatingKind | null } = $props();

	const isReferee = $derived(kind === 'referee');
	const SHADES = ['bg-ink', 'bg-club', 'bg-ice', 'bg-muted'];
	const typeName = (n: string) => n || t('stats.off.type.none');
	const types = $derived(stats?.types ?? []);
	const shade = (i: number) => SHADES[Math.min(i, SHADES.length - 1)];
	const eur = (n: string | number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(Number(n));

	const monthItems = $derived(
		(stats?.months ?? []).map((m) => ({
			key: m.month,
			label: monthLabel(m.month),
			segments: m.counts.map((value, i) => ({ value, cls: shade(i) })),
			tip: [monthLabel(m.month, true), m.counts.map((n, i) => `${n} ${typeName(types[i])}`).join(' · ')]
		}))
	);
	const seasonItems = $derived(
		(stats?.seasons ?? []).map((s, i, all) => ({ key: s.season.id, label: s.season.name, segments: [{ value: s.games, cls: i === all.length - 1 ? 'bg-club' : 'bg-ink' }], tip: [s.season.name, String(s.games)] }))
	);
	const total = $derived(stats?.games ?? 0);
	const top = $derived(Math.max(1, ...(stats?.competitions ?? []).map((c) => c.games)));
	const competitions = $derived(
		(stats?.competitions ?? []).map((c, i) => ({
			key: `${c.competition}|${c.teams}`,
			label: `${c.competition || t('stats.off.other_competition')} ${c.teams}`.trim(),
			value: String(c.games),
			pct: (c.games / top) * 100,
			cls: i % 2 ? 'bg-club' : 'bg-ink',
			sub: `${c.games} · ${Math.round((c.games / Math.max(1, total)) * 100)}%`
		}))
	);
</script>

<div class="card card-body gap-4">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h3 class="card-title"><Whistle size={18} aria-hidden="true" /> {isReferee ? t('stats.off.referee') : t('stats.off.official')}{#if profile?.level}<span class="badge badge-outline badge-sm">{profile.level.name}</span>{/if}</h3>
	</div>
	{#if !stats}
		<p class="py-4 text-center text-sm text-muted">{t('stats.off.empty')}</p>
	{:else}
		<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
			<Tile value={String(stats.games_this_season)} label={t('stats.off.games')} />
			<Tile value={String(stats.games_per_month)} label={t('stats.off.per_month')} />
			{#if isReferee}
				<Tile value={stats.penalties_per_game === null ? '–' : String(stats.penalties_per_game)} label={t('stats.off.penalties_per_game')} />
			{:else}
				<Tile value={String(stats.games)} label={t('stats.off.total')} />
			{/if}
			<Tile value={eur(stats.fees)} label={t('stats.off.fees')} accent />
		</div>
		<div class="grid gap-4 lg:grid-cols-2">
			<div class="grid gap-2">
				<h4 class="flex flex-wrap items-center gap-2 font-display text-sm font-bold tracking-[.08em] text-ink uppercase"><CalendarDays size={16} class="shrink-0" aria-hidden="true" /> {t('stats.off.months')}</h4>
				<BarChart items={monthItems} label={t('stats.off.months')} height="h-32" />
				<div class="flex flex-wrap gap-x-4 text-xs text-muted">
					{#each types as ty, i (ty)}<span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-sm {shade(i)}"></span>{typeName(ty)}</span>{/each}
				</div>
			</div>
			<div class="grid gap-2">
				<h4 class="flex flex-wrap items-center gap-2 font-display text-sm font-bold tracking-[.08em] text-ink uppercase"><CalendarRange size={16} class="shrink-0" aria-hidden="true" /> {t('stats.off.seasons')}</h4>
				<BarChart items={seasonItems} label={t('stats.off.seasons')} height="h-32" />
			</div>
		</div>
		<div class="grid gap-2">
			<h4 class="flex flex-wrap items-center gap-2 font-display text-sm font-bold tracking-[.08em] text-ink uppercase"><Trophy size={16} class="shrink-0" aria-hidden="true" /> {t('stats.off.competitions')}</h4>
			<HBars rows={competitions} label={t('stats.off.competitions')} />
		</div>
	{/if}
</div>
