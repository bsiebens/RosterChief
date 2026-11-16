<script lang="ts">
	import { ChartColumn, ShieldHalf } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { num, shortDate } from '#lib/analytics.ts';
	import { seasonLabel, type GoalieStats } from '#lib/member-stats.ts';
	import BarChart from './BarChart.svelte';
	import Tile from './Tile.svelte';

	/** A goalie's numbers: totals, goals-against average per season and goals against per game. */
	let { goalie }: { goalie: GoalieStats } = $props();

	const seasonItems = $derived(
		goalie.seasons.map((s, i) => ({
			key: `${s.season?.id ?? 'none'}-${i}`,
			label: seasonLabel(s.season),
			top: num(s.gaa, 2),
			segments: [{ value: s.gaa ?? 0, cls: 'bg-ink' }],
			tip: [seasonLabel(s.season), `${t('stats.goalie.gaa')} ${num(s.gaa, 2)}`, `${s.games} ${t('gamestats.games')} · ${s.conceded} ${t('stats.goalie.ga')} · ${s.shutouts} ${t('stats.goalie.shutouts')}`]
		}))
	);
	const formItems = $derived(
		goalie.form.map((g) => ({
			key: g.event.id,
			label: shortDate(g.event.start),
			segments: [{ value: g.conceded, cls: g.conceded === 0 ? 'bg-ok' : 'bg-club' }],
			tip: [g.event.opponent ? t('stats.vs', { name: g.event.opponent }) : g.event.title, `${shortDate(g.event.start)} · ${g.event.is_home_game ? t('analytics.home') : t('analytics.away')}`, `${g.conceded} ${t('stats.goalie.ga')}${g.minutes ? ` · ${g.minutes} min` : ''}`]
		}))
	);
</script>

<div class="grid gap-4">
	<div class="grid grid-cols-2 gap-2 sm:grid-cols-5">
		<Tile value={goalie.games} label={t('gamestats.games')} />
		<Tile value={goalie.conceded} label={t('stats.goalie.ga')} />
		<Tile value={num(goalie.gaa, 2)} label={t('stats.goalie.gaa')} hint={t('stats.goalie.gaa_help')} accent />
		<Tile value={goalie.minutes ?? '–'} label={t('stats.goalie.minutes')} />
		<Tile value={goalie.shutouts} label={t('stats.goalie.shutouts')} />
	</div>
	<div class="grid gap-4 lg:grid-cols-2">
		<div class="card card-body gap-2">
			<h3 class="card-title text-base"><ShieldHalf size={16} aria-hidden="true" /> {t('stats.goalie.seasons')}</h3>
			<BarChart items={seasonItems} label={t('stats.goalie.seasons')} />
			<p class="text-xs text-muted">{t('stats.goalie.gaa_help')}</p>
		</div>
		<div class="card card-body gap-2">
			<h3 class="card-title text-base"><ChartColumn size={16} aria-hidden="true" /> {t('stats.goalie.form', { n: goalie.form.length })}</h3>
			<BarChart items={formItems} label={t('stats.goalie.form', { n: goalie.form.length })} />
			<p class="text-xs text-muted"><span class="mr-1 inline-block size-2.5 rounded-sm bg-ok align-middle"></span>{t('stats.goalie.shutouts')}</p>
		</div>
	</div>
	<p class="text-xs text-muted">{t('stats.goalie.no_saves')}</p>
</div>
