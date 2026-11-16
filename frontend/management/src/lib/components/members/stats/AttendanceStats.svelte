<script lang="ts">
	import { CalendarCheck, CalendarRange, Flame, Layers } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatRate, kindLabel, rateTone, TONE } from '#lib/attendance.ts';
	import { monthLabel, type AttendanceStats } from '#lib/member-stats.ts';
	import BarChart from './BarChart.svelte';
	import HBars from './HBars.svelte';
	import Tile from './Tile.svelte';

	/** Turnout over time: the rate ring, a bar per month (the last 12), the rate per kind of event and the present-streaks. */
	let { attendance }: { attendance: AttendanceStats } = $props();

	const tone = $derived(TONE[rateTone(attendance.rate)]);
	const monthItems = $derived(
		attendance.months.map((m) => ({
			key: m.month,
			label: monthLabel(m.month),
			top: m.rate === null ? '' : `${m.rate}`,
			segments: [{ value: m.rate ?? 0, cls: TONE[rateTone(m.rate)].bar }],
			tip: [monthLabel(m.month, true), m.rate === null ? t('stats.att.none') : `${formatRate(m.rate)}`, t('stats.att.tip', { present: m.present, absent: m.absent })]
		}))
	);
	const kinds = $derived(attendance.kinds.map((k) => ({ key: k.kind, label: kindLabel(k.kind), value: formatRate(k.rate), pct: k.rate ?? 0, cls: TONE[rateTone(k.rate)].bar, sub: t('stats.att.tip', { present: k.present, absent: k.absent }) })));
	const ring = $derived(attendance.rate ?? 0);
</script>

<div class="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
	<div class="card card-body items-center gap-3 text-center">
		<h3 class="card-title text-base"><CalendarCheck size={16} aria-hidden="true" /> {t('stats.att.rate')}</h3>
		<div class="relative size-32">
			<svg class="size-full -rotate-90" viewBox="0 0 100 100" aria-hidden="true">
				<circle cx="50" cy="50" r="44" fill="none" class="stroke-rule" stroke-width="9" />
				{#if ring}<circle cx="50" cy="50" r="44" fill="none" class="{attendance.rate !== null && attendance.rate >= 80 ? 'stroke-ok' : attendance.rate !== null && attendance.rate >= 60 ? 'stroke-warn' : 'stroke-danger'}" stroke-width="9" stroke-linecap="round" pathLength="100" stroke-dasharray="{ring} 100" />{/if}
			</svg>
			<span class="absolute inset-0 flex items-center justify-center font-display text-3xl font-extrabold {tone.text}">{formatRate(attendance.rate)}</span>
		</div>
		<p class="text-xs text-muted">{t('stats.att.tip', { present: attendance.present, absent: attendance.absent })}</p>
		<div class="grid w-full grid-cols-2 gap-2">
			<Tile value={attendance.current_streak} label={t('stats.att.streak')} hint={t('stats.att.streak_help')} accent={attendance.current_streak >= 5} />
			<Tile value={attendance.longest_streak} label={t('stats.att.longest')} hint={t('stats.att.streak_help')} />
		</div>
	</div>
	<div class="grid gap-4">
		<div class="card card-body gap-2">
			<h3 class="card-title text-base"><CalendarRange size={16} aria-hidden="true" /> {t('stats.att.months')}</h3>
			<BarChart items={monthItems} label={t('stats.att.months')} max={100} />
			<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
				{#each ['ok', 'warn', 'danger'] as const as k (k)}<span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-sm {TONE[k].bar}"></span>{t(`stats.att.band.${k}`)}</span>{/each}
				<span class="ml-auto inline-flex items-center gap-1"><Flame size={12} aria-hidden="true" />{t('stats.att.help')}</span>
			</div>
		</div>
		<div class="card card-body gap-3">
			<h3 class="card-title text-base"><Layers size={16} aria-hidden="true" /> {t('stats.att.kinds')}</h3>
			<HBars rows={kinds} label={t('stats.att.kinds')} />
		</div>
	</div>
</div>
