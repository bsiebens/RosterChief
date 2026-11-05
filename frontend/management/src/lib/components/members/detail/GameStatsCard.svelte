<script lang="ts">
	import { Goal } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { analyticsHref, num, type MemberGameStats } from '#lib/analytics.ts';

	/** A member's own numbers from the game sheets (every season), with one row per team and season. Only shown when there are any. */
	let { stats }: { stats: MemberGameStats } = $props();
	const tiles = $derived([
		{ key: 'games', value: stats.games },
		{ key: 'goals', value: stats.goals },
		{ key: 'assists', value: stats.assists },
		{ key: 'points', value: stats.points },
		{ key: 'pim', value: stats.penalty_minutes },
		{ key: 'ppg', value: stats.pp_goals },
		{ key: 'gwg', value: stats.game_winning_goals }
	]);
</script>

<div class="card card-body gap-3">
	<h2 class="card-title"><Goal size={18} aria-hidden="true" /> {t('gamestats.title')}</h2>
	<div class="grid grid-cols-4 gap-2">
		{#each tiles as tile (tile.key)}
			<div class="rounded-lg bg-paper px-2 py-2 text-center">
				<p class="font-display text-2xl leading-none font-extrabold text-ink">{tile.value}</p>
				<p class="mt-1 text-[11px] text-muted">{t(`gamestats.${tile.key}`)}</p>
			</div>
		{/each}
	</div>
	{#if stats.goalie}
		<p class="text-sm text-slate">{t('gamestats.goalie', { games: stats.goalie.games, conceded: stats.goalie.conceded })}{#if stats.goalie.minutes !== null}&nbsp;· {t('gamestats.goalie_time', { minutes: stats.goalie.minutes, gaa: num(stats.goalie.gaa, 2) })}{/if}</p>
	{/if}
	{#if stats.teams.length}
		<div class="overflow-x-auto">
			<table class="table text-sm">
				<thead>
					<tr><th>{t('gamestats.col.team')}</th><th class="text-right" title={t('gamestats.games')}>{t('gamestats.abbr.gp')}</th><th class="text-right" title={t('gamestats.goals')}>{t('gamestats.abbr.g')}</th><th class="text-right" title={t('gamestats.assists')}>{t('gamestats.abbr.a')}</th><th class="text-right" title={t('gamestats.points')}>{t('gamestats.abbr.p')}</th><th class="text-right" title={t('gamestats.pim')}>{t('gamestats.abbr.pim')}</th></tr>
				</thead>
				<tbody>
					{#each stats.teams as row (`${row.team.id}-${row.season?.id ?? ''}`)}
						<tr>
							<td>
								<a class="link font-semibold" href={analyticsHref(row.team.id, row.season?.id)}>{row.team.name}</a>
								{#if row.season}<p class="text-xs text-muted">{row.season.name}</p>{/if}
							</td>
							<td class="text-right font-mono">{row.games}</td>
							<td class="text-right font-mono">{row.goals}</td>
							<td class="text-right font-mono">{row.assists}</td>
							<td class="text-right font-mono font-bold">{row.points}</td>
							<td class="text-right font-mono">{row.penalty_minutes}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
