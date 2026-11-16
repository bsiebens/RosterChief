<script lang="ts">
	import { Goal } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { analyticsHref, num, type MemberGameStats } from '#lib/analytics.ts';

	/** A member's own numbers from the game sheets (every season), with one row per team and season. Only shown when there are any. */
	/**
	 * `links` off (the public page): team names are plain text, not links into the management app.
	 * `load`: fetches the numbers of one season; with it a season picker (all seasons by default) sits top right. The picker
	 * lists the seasons of the numbers handed in (`stats`, all seasons).
	 */
	let { stats, links = true, load }: { stats: MemberGameStats; links?: boolean; load?: (seasonId: string) => Promise<MemberGameStats | null> } = $props();

	let choice = $state('');
	let picked = $state<MemberGameStats | null>(null);
	let busy = $state(false);
	let failed = $state(false);
	const seasons = $derived.by(() => {
		const seen = new Map<string, string>();
		for (const row of stats.teams) if (row.season && !seen.has(row.season.id)) seen.set(row.season.id, row.season.name);
		return [...seen].map(([id, name]) => ({ id, name }));
	});
	// New numbers handed in (after a refresh): back to all seasons.
	$effect(() => {
		void stats;
		choice = '';
		picked = null;
		failed = false;
	});
	const shown = $derived(choice && picked ? picked : stats);

	async function onPick() {
		failed = false;
		if (!choice || !load) {
			picked = null;
			return;
		}
		const wanted = choice;
		busy = true;
		const res = await load(wanted);
		if (choice !== wanted) return;
		busy = false;
		if (res) picked = res;
		else {
			failed = true;
			choice = '';
			picked = null;
		}
	}
	const tiles = $derived([
		{ key: 'games', value: shown.games },
		{ key: 'goals', value: shown.goals },
		{ key: 'assists', value: shown.assists },
		{ key: 'points', value: shown.points },
		{ key: 'penalties', value: shown.penalties },
		{ key: 'pim', value: shown.penalty_minutes },
		{ key: 'ppg', value: shown.pp_goals },
		{ key: 'gwg', value: shown.game_winning_goals }
	]);
</script>

<div class="card card-body gap-3">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h2 class="card-title"><Goal size={18} aria-hidden="true" /> {t('gamestats.title')}</h2>
		{#if load && seasons.length}
			<select class="select select-sm w-auto max-w-full" aria-label={t('gamestats.season')} bind:value={choice} onchange={onPick} disabled={busy}>
				<option value="">{t('gamestats.all_seasons')}</option>
				{#each seasons as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
			</select>
		{/if}
	</div>
	{#if failed}<p class="text-xs text-error" role="alert">{t('gamestats.season_failed')}</p>{/if}
	<div class="grid grid-cols-4 gap-2 transition-opacity {busy ? 'opacity-50' : ''}">
		{#each tiles as tile (tile.key)}
			<div class="rounded-lg bg-paper px-2 py-2 text-center">
				<p class="font-display text-2xl leading-none font-extrabold text-ink">{tile.value}</p>
				<p class="mt-1 text-[11px] text-muted">{t(`gamestats.${tile.key}`)}</p>
			</div>
		{/each}
	</div>
	{#if shown.goalie}
		<p class="text-sm text-slate">{t('gamestats.goalie', { games: shown.goalie.games, conceded: shown.goalie.conceded })}{#if shown.goalie.minutes !== null}&nbsp;· {t('gamestats.goalie_time', { minutes: shown.goalie.minutes, gaa: num(shown.goalie.gaa, 2) })}{/if}</p>
	{/if}
	{#if shown.teams.length}
		<div class="overflow-x-auto">
			<table class="table w-full table-fixed text-sm">
				<thead>
					<tr><th class="w-[30%]">{t('gamestats.col.team')}</th><th class="w-[11%] text-right" title={t('gamestats.games')}>{t('gamestats.abbr.gp')}</th><th class="w-[11%] text-right" title={t('gamestats.goals')}>{t('gamestats.abbr.g')}</th><th class="w-[11%] text-right" title={t('gamestats.assists')}>{t('gamestats.abbr.a')}</th><th class="w-[11%] text-right" title={t('gamestats.points')}>{t('gamestats.abbr.p')}</th><th class="w-[11%] text-right" title={t('gamestats.pim')}>{t('gamestats.abbr.pim')}</th></tr>
				</thead>
				<tbody>
					{#each shown.teams as row (`${row.team.id}-${row.season?.id ?? ''}`)}
						<tr>
							<td>
								{#if links}<a class="link font-semibold" href={analyticsHref(row.team.id, row.season?.id)}>{row.team.name}</a>{:else}<span class="font-semibold">{row.team.name}</span>{/if}
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
