<script lang="ts">
	import { ChartColumn, Flame, Home, Plane, TrendingUp } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { num, shortDate } from '#lib/analytics.ts';
	import { seasonLabel, type PlayerStats } from '#lib/member-stats.ts';
	import BarChart from './BarChart.svelte';
	import HBars from './HBars.svelte';
	import Tile from './Tile.svelte';

	/** A skater's charts: points per season (goals + assists stacked), the last games, streaks and the home/away split. */
	let { player, links = true }: { player: PlayerStats; links?: boolean } = $props();

	const seasonItems = $derived(
		player.seasons.map((s, i) => ({
			key: `${s.season?.id ?? 'none'}-${i}`,
			label: seasonLabel(s.season),
			segments: [
				{ value: s.goals, cls: 'bg-ink' },
				{ value: s.assists, cls: 'bg-club' }
			],
			tip: [seasonLabel(s.season), `${s.goals} ${t('gamestats.goals')} · ${s.assists} ${t('gamestats.assists')}`, `${s.points} ${t('gamestats.points')} · ${s.games} ${t('gamestats.games')}`, `${num(s.games ? s.points / s.games : null, 2)} ${t('stats.ppg')}`]
		}))
	);
	const formItems = $derived(
		player.form.map((g) => ({
			key: g.event.id,
			label: shortDate(g.event.start),
			segments: [
				{ value: g.goals, cls: 'bg-ink' },
				{ value: g.assists, cls: 'bg-club' }
			],
			tip: [g.event.opponent ? t('stats.vs', { name: g.event.opponent }) : g.event.title, `${shortDate(g.event.start)} · ${g.event.is_home_game ? t('analytics.home') : t('analytics.away')}${g.event.score_for !== null ? ` · ${g.event.score_for}–${g.event.score_against}` : ''}`, `${g.goals} ${t('gamestats.goals')} · ${g.assists} ${t('gamestats.assists')}${g.penalty_minutes ? ` · ${g.penalty_minutes} ${t('gamestats.pim')}` : ''}`]
		}))
	);
	const maxPoints = $derived(Math.max(1, player.home.points, player.away.points));
	const split = $derived(
		[
			{ key: 'home', label: t('analytics.home'), s: player.home, cls: 'bg-ink' },
			{ key: 'away', label: t('analytics.away'), s: player.away, cls: 'bg-club' }
		].map((r) => ({ key: r.key, label: r.label, value: `${r.s.points} ${t('stats.pts')}`, pct: (r.s.points / maxPoints) * 100, cls: r.cls, sub: `${r.s.games} ${t('gamestats.games')} · ${r.s.goals} ${t('gamestats.goals')} · ${r.s.assists} ${t('gamestats.assists')} · ${num(r.s.games ? r.s.points / r.s.games : null, 2)} ${t('stats.ppg')}` }))
	);
</script>

<div class="grid gap-4">
	<div class="grid gap-4 lg:grid-cols-2">
		<div class="card card-body gap-2">
			<h3 class="card-title text-base"><TrendingUp size={16} aria-hidden="true" /> {t('stats.seasons.title')}</h3>
			<BarChart items={seasonItems} label={t('stats.seasons.title')} />
			<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
				<span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-sm bg-ink"></span>{t('gamestats.goals')}</span>
				<span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-sm bg-club"></span>{t('gamestats.assists')}</span>
				<span class="ml-auto">{t('stats.seasons.help')}</span>
			</div>
		</div>
		<div class="card card-body gap-2">
			<h3 class="card-title text-base"><ChartColumn size={16} aria-hidden="true" /> {t('stats.form.title', { n: player.form.length })}</h3>
			<BarChart items={formItems} label={t('stats.form.title', { n: player.form.length })} />
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
				<span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-sm bg-ink"></span>{t('gamestats.goals')}</span>
				<span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-sm bg-club"></span>{t('gamestats.assists')}</span>
				<span class="ml-auto">{t('stats.form.help')}</span>
			</div>
		</div>
	</div>

	<div class="grid gap-4 lg:grid-cols-2">
		<div class="card card-body gap-3">
			<h3 class="card-title text-base"><Home size={16} aria-hidden="true" /><Plane size={16} aria-hidden="true" /> {t('stats.split.title')}</h3>
			<HBars rows={split} label={t('stats.split.title')} />
		</div>
		<div class="card card-body gap-3">
			<h3 class="card-title text-base"><Flame size={16} aria-hidden="true" /> {t('stats.highlights')}</h3>
			<div class="grid grid-cols-2 gap-2">
				<Tile value={player.multi_point_games} label={t('stats.multi')} />
				<Tile value={player.best_game ? `${player.best_game.points} ${t('stats.pts')}` : '–'} label={t('stats.best')} />
			</div>
			{#if player.best_game}
				<p class="text-sm text-slate">
					{#if links}<a class="link font-semibold" href="/calendar/events/{player.best_game.event.id}">{player.best_game.event.opponent ? t('stats.vs', { name: player.best_game.event.opponent }) : player.best_game.event.title}</a>{:else}<span class="font-semibold">{player.best_game.event.opponent ? t('stats.vs', { name: player.best_game.event.opponent }) : player.best_game.event.title}</span>{/if}
					· {shortDate(player.best_game.event.start)} · {player.best_game.goals} {t('gamestats.goals')}
				</p>
			{/if}
		</div>
	</div>
</div>
