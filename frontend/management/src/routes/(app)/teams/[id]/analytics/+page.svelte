<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ArrowLeft, BarChart3, Check, Clock, Crosshair, Goal, House, Plane, ShieldHalf, Swords, TriangleAlert, Trophy, Wrench } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ForAgainstBars from '#lib/components/analytics/ForAgainstBars.svelte';
	import Leaderboard from '#lib/components/analytics/Leaderboard.svelte';
	import Ring from '#lib/components/analytics/Ring.svelte';
	import TrendChart from '#lib/components/analytics/TrendChart.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { eventHref, formatEventDate, memberHref } from '#lib/attendance.ts';
	import { num, pct, RESULT_CHIP, SCOPES, shortDate, signed, type ForAgainst, type Scope, type SplitRow } from '#lib/analytics.ts';

	let { data, form } = $props();
	const a = $derived(data.analytics);
	const teamHref = $derived(`/teams/${data.team.id}${data.season ? `?season=${data.season}` : ''}`);
	const seasonValue = $derived(data.season || a?.season.id || data.team.season_id || '');

	usePageHeader(() => ({ title: t('analytics.title_for', { team: data.team.name }), context: a?.season.name ?? t('analytics.context'), actions }));

	/** Changes one filter in the URL, keeping the others. */
	function filter(changes: Record<string, string | null>) {
		const params = new URLSearchParams(page.url.search);
		for (const [k, v] of Object.entries(changes)) {
			if (v === null || v === '') params.delete(k);
			else params.set(k, v);
		}
		const qs = params.toString();
		goto(qs ? `?${qs}` : '?', { reset: false });
	}
	const setScope = (scope: Scope) => filter({ scope: scope === 'all' ? null : scope });

	const resultLabel = (r: string) => t(`analytics.result.${r}`);
	const periodLabel = (row: ForAgainst) => (/^\d+$/.test(row.label) ? t('analytics.period_n', { n: row.label }) : row.label);
	const periodAxis = (row: ForAgainst) => (/^\d+$/.test(row.label) ? `P${row.label}` : row.label);
	const minuteLabel = (row: ForAgainst) => (row.label === '60+' ? t('analytics.overtime') : t('analytics.minutes_n', { range: row.label.replace('-', '–') }));
	const minuteAxis = (row: ForAgainst) => (row.label === '60+' ? 'OT' : `${row.label.split('-')[0]}'`);
	// A source's own exotic code (not one of ours) stays as it is.
	const strengthLabel = (code: string) => (['EQ', 'PP1', 'PP2', 'SH1', 'SH2'].includes(code) ? t(`analytics.strength.${code}`) : code);
	const splits = $derived(a ? ([{ key: 'home', row: a.split.home, Icon: House }, { key: 'away', row: a.split.away, Icon: Plane }] as { key: string; row: SplitRow; Icon: typeof House }[]) : []);
	const hasSheets = $derived(!!a && a.timeline_games > 0);
	const showCompetitions = $derived(!!a && (a.competitions.length > 1 || !!data.competition));
	let busyKey = $state<string | null>(null);
</script>

{#snippet actions()}
	<select class="select w-auto" aria-label={t('analytics.season')} value={seasonValue} onchange={(e) => filter({ season: e.currentTarget.value, competition: null })}>
		{#each data.seasons ?? [] as s (s.id)}<option value={s.id}>{s.name}{s.is_current ? ` (${t('members.list.current')})` : ''}</option>{/each}
	</select>
	<a class="btn btn-outline gap-2" href={teamHref}><ArrowLeft size={16} /> {t('analytics.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('analytics.title_for', { team: data.team.name }))}</title></svelte:head>

{#if data.unavailable}
	<div class="card card-body mx-auto max-w-xl items-center gap-3 py-10 text-center">
		<BarChart3 size={32} class="text-muted" aria-hidden="true" />
		<h2 class="font-display text-2xl font-extrabold text-ink uppercase">{t('analytics.unavailable.title')}</h2>
		<p class="text-sm text-muted">{t('analytics.unavailable.body')}</p>
		<a class="btn btn-primary mt-2 gap-2" href={teamHref}><ArrowLeft size={16} /> {t('analytics.back')}</a>
	</div>
{:else}
	<div class="grid gap-5">
		<!-- Filters: which games count. -->
		<div class="card flex flex-wrap items-center gap-3 px-4 py-3">
			<div class="flex items-center gap-1 rounded-full bg-subhead p-1" role="group" aria-label={t('analytics.scope')}>
				{#each SCOPES as scope (scope)}
					<button type="button" class="rounded-full px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {data.scope === scope ? 'bg-ink text-white' : 'text-muted hover:text-ink'}" aria-pressed={data.scope === scope} onclick={() => setScope(scope)}>{t(`analytics.scope.${scope}`)}</button>
				{/each}
			</div>
			<label class="flex cursor-pointer items-center gap-2 text-sm text-slate">
				<input type="checkbox" class="checkbox" checked={data.friendlies} onchange={(e) => filter({ friendlies: e.currentTarget.checked ? null : '0' })} />
				{t('analytics.friendlies')}
			</label>
			{#if showCompetitions && a}
				<select class="select select-sm w-auto" aria-label={t('analytics.competition')} value={data.competition} onchange={(e) => filter({ competition: e.currentTarget.value })}>
					<option value="">{t('analytics.all_competitions')}</option>
					{#each a.competitions as name (name)}<option value={name}>{name}</option>{/each}
					{#if data.competition && !a.competitions.includes(data.competition)}<option value={data.competition}>{data.competition}</option>{/if}
				</select>
			{/if}
		</div>

		{#if data.problem}
			<Alert kind={data.problem === 'season' ? 'info' : 'error'}>{t(`analytics.problem.${data.problem}`)}</Alert>
		{:else if !a || a.games === 0}
			<div class="card card-body items-center gap-2 py-10 text-center">
				<Goal size={28} class="text-muted" aria-hidden="true" />
				<p class="font-semibold text-ink">{t('analytics.empty.title')}</p>
				<p class="text-sm text-muted">{data.scope !== 'all' || !data.friendlies || data.competition ? t('analytics.empty.filtered') : t('analytics.empty.body')}</p>
			</div>
		{:else}
			{#if a.strength_warnings.length}
				<section class="card card-body gap-3 border-warn-border bg-warn-bg" aria-labelledby="strength-title">
					<h2 id="strength-title" class="card-title text-warn-deep"><TriangleAlert size={18} aria-hidden="true" /> {t('analytics.strength.title')} <span class="font-mono text-xs font-normal">· {a.strength_warnings.length}</span></h2>
					<p class="text-sm text-warn-text">{t('analytics.strength.help')}</p>
					<ul class="divide-y divide-warn-border">
						{#each a.strength_warnings as w (`${w.event.id}/${w.goal}`)}
							{@const key = `${w.event.id}/${w.goal}`}
							<li class="flex flex-wrap items-center gap-x-6 gap-y-3 py-3">
								<div class="min-w-0 flex-1 text-sm">
									<a class="link font-semibold" href={eventHref(w.event.id)}>{w.event.opponent ?? w.event.title} · {shortDate(w.event.start)}</a>
									<p class="text-slate">{w.ours ? t('analytics.strength.ours') : t('analytics.strength.theirs')} · {t('analytics.strength.when', { period: w.period, time: w.time })}{#if w.player}&nbsp;· {w.player}{/if}</p>
									<p class="text-ink">{t('analytics.strength.mismatch', { recorded: strengthLabel(w.recorded), expected: strengthLabel(w.expected) })}</p>
									{#if w.penalties.length}
										<p class="text-xs text-muted">{t('analytics.strength.in_box')} {w.penalties.map((p) => `${p.time} ${p.ours ? t('analytics.us') : t('analytics.them')}${p.player ? ` ${p.player}` : ''}${p.kind ? ` (${p.kind.replace('_', ' ')})` : ''}`).join(' · ')}</p>
									{/if}
									{#if form?.key === key && form?.strengthError}<p class="mt-1 text-xs text-danger">{form.strengthError === 'forbidden' ? t('analytics.strength.forbidden') : t('analytics.strength.failed')}</p>{/if}
								</div>
								{#if w.can_fix}
									<form
										method="POST"
										action="?/strength"
										class="flex flex-wrap gap-2"
										use:enhance={() => {
											busyKey = key;
											return async ({ update }) => {
												await update({ reset: false });
												busyKey = null;
											};
										}}>
										<input type="hidden" name="event" value={w.event.id} />
										<input type="hidden" name="goal" value={w.goal} />
										<button class="btn btn-primary btn-sm gap-1.5" type="submit" name="strength" value={w.expected} disabled={busyKey === key}><Wrench size={14} aria-hidden="true" /> {t('analytics.strength.count_as', { strength: strengthLabel(w.expected) })}</button>
										<button class="btn btn-outline btn-sm gap-1.5" type="submit" name="strength" value={w.keep} disabled={busyKey === key}><Check size={14} aria-hidden="true" /> {t('analytics.strength.keep')}</button>
									</form>
								{/if}
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			<!-- Hero: record and points, goals, the running goal difference. -->
			<section class="card grid gap-6 border-ink bg-ink p-6 text-white lg:grid-cols-[1fr_1.1fr_1.4fr] lg:gap-0">
				<div class="flex flex-col justify-between gap-6 lg:pr-8">
					<div>
						<p class="font-mono text-[11px] tracking-[.14em] text-on-dark uppercase">{a.games === 1 ? t('analytics.record_one') : t('analytics.record', { n: a.games })}</p>
						<p class="mt-2 font-display text-7xl leading-none font-extrabold tracking-tight">{a.record.won}–{a.record.drawn}–{a.record.lost}</p>
						<p class="mt-2 text-sm text-on-dark">{t('analytics.points', { n: a.points_table })}</p>
					</div>
					<div>
						<p class="font-mono text-[11px] tracking-[.14em] text-on-dark uppercase">{t('analytics.form')}</p>
						<div class="mt-2 flex gap-2">
							{#each a.form as game, i (`${game.event.id}-${i}`)}
								<a href={eventHref(game.event.id)} class="flex size-10 items-center justify-center rounded-lg font-display text-xl font-extrabold {game.result === 'W' ? 'bg-ok text-white' : game.result === 'L' ? 'bg-club text-club-content' : 'bg-steel text-on-dark'}" title="{game.event.opponent ?? game.event.title} · {shortDate(game.event.start)} · {game.event.score_for}–{game.event.score_against}">{resultLabel(game.result)}</a>
							{/each}
						</div>
					</div>
				</div>
				<div class="flex flex-col justify-between gap-6 lg:border-l lg:border-hairline lg:px-8">
					<div>
						<p class="font-mono text-[11px] tracking-[.14em] text-on-dark uppercase">{t('analytics.goals_fa')}</p>
						<p class="mt-2 flex items-baseline gap-3 font-display text-7xl leading-none font-extrabold">
							<span>{a.goals_for}</span><span class="text-3xl font-medium text-on-dark-faint">/</span><span class="text-club">{a.goals_against}</span>
							<span class="ml-auto text-3xl {a.goal_difference < 0 ? 'text-club' : 'text-white'}" title={t('analytics.difference')}>{signed(a.goal_difference)}</span>
						</p>
						{#if a.goals_for + a.goals_against}
							<div class="mt-3 flex gap-1.5" aria-hidden="true">
								<span class="h-2 rounded-full bg-white" style="flex: {a.goals_for} 1 0"></span>
								<span class="h-2 rounded-full bg-club" style="flex: {a.goals_against} 1 0"></span>
							</div>
						{/if}
					</div>
					<div class="flex flex-wrap gap-x-8 gap-y-3">
						<div><p class="font-mono text-xl font-semibold">{num(a.avg_for, 2)}</p><p class="text-sm text-on-dark">{t('analytics.avg_for')}</p></div>
						<div><p class="font-mono text-xl font-semibold text-club">{num(a.avg_against, 2)}</p><p class="text-sm text-on-dark">{t('analytics.avg_against')}</p></div>
						<div><p class="font-mono text-xl font-semibold">{a.clean_sheets}</p><p class="text-sm text-on-dark">{t('analytics.clean_sheets')}</p></div>
					</div>
				</div>
				<div class="flex flex-col gap-3 lg:border-l lg:border-hairline lg:pl-8">
					<div class="flex items-center justify-between gap-2">
						<p class="font-mono text-[11px] tracking-[.14em] text-on-dark uppercase">{t('analytics.trend.title')}</p>
						{#if a.trend}<span class="rounded-full bg-steel px-2.5 py-0.5 text-xs font-semibold">{t('analytics.trend.last', { diff: signed(a.trend.last) })}</span>{/if}
					</div>
					{#if a.trend}<TrendChart trend={a.trend} />{/if}
				</div>
			</section>

			{#if a.timeline_games < a.games}<p class="text-sm text-muted">{t('analytics.cover.sheets', { n: a.timeline_games, total: a.games })}</p>{/if}
			{#if a.shot_games && a.shot_games < a.games}<p class="text-sm text-muted">{t('analytics.cover.shots', { n: a.shot_games, total: a.games })}</p>{/if}

			<!-- Special teams, discipline, shooting. -->
			<div class="grid gap-4 lg:grid-cols-3">
				<section class="card card-body gap-4">
					<h2 class="card-title"><Swords size={18} aria-hidden="true" /> {t('analytics.special')}</h2>
					<div class="grid grid-cols-2 gap-4">
						<Ring value={a.pp_pct} label={t('analytics.pp')} detail={t('analytics.pp_detail', { goals: a.pp_goals_for, chances: a.pp_opportunities })} />
						<Ring value={a.pk_pct} label={t('analytics.pk')} detail={t('analytics.pk_detail', { killed: a.pk_killed, total: a.pk_opportunities })} />
					</div>
					<div class="mt-auto rounded-xl bg-paper p-3">
						<p class="text-xs text-muted">{t('analytics.pp_goals_fa')}</p>
						<p class="mt-1 font-mono text-xl font-semibold text-ink">{a.pp_goals_for} <span class="text-dim">/</span> <span class="text-club-dark">{a.pp_goals_against}</span></p>
					</div>
				</section>

				<section class="card card-body gap-3">
					<h2 class="card-title"><Clock size={18} aria-hidden="true" /> {t('analytics.discipline')}</h2>
					<div class="grid grid-cols-2 gap-3">
						<div class="rounded-xl bg-paper p-3">
							<p class="text-xs text-muted">{t('analytics.pim_fa')}</p>
							<p class="mt-1 font-mono text-xl font-semibold text-ink">{a.pim_for} <span class="text-dim">/</span> <span class="text-club-dark">{a.pim_against}</span></p>
						</div>
						<div class="rounded-xl bg-paper p-3">
							<p class="text-xs text-muted">{t('analytics.avg_pim')}</p>
							<p class="mt-1 font-mono text-xl font-semibold text-ink">{num(a.avg_pim)}</p>
						</div>
					</div>
					{#if a.game_misconduct_minutes}<p class="text-sm text-slate">{t('analytics.gm_note', { minutes: a.game_misconduct_minutes, avg: num(a.avg_pim_excl_game_misconducts) })}</p>{/if}
					{#if hasSheets && a.game_rows.some((r) => r.pim_for !== null)}
						<div class="mt-1 flex h-24 items-end gap-1.5 border-b-2 border-ink" aria-label={t('analytics.pim_per_game')} role="img">
							{#each [...a.game_rows].reverse() as row (row.event.id)}
								<div class="flex h-full flex-1 items-end justify-center gap-0.5" title="{row.event.opponent ?? row.event.title} · {shortDate(row.event.start)}: {row.pim_for ?? '—'} / {row.pim_against ?? '—'}">
									<div class="w-2 min-h-[2px] rounded-t-sm bg-ink" style="height: {row.pim_for_pct}%"></div>
									<div class="w-2 min-h-[2px] rounded-t-sm bg-club" style="height: {row.pim_against_pct}%"></div>
								</div>
							{/each}
						</div>
						<p class="text-xs text-muted">{t('analytics.pim_per_game')}</p>
					{/if}
				</section>

				<section class="card card-body gap-3">
					<h2 class="card-title"><Crosshair size={18} aria-hidden="true" /> {t('analytics.shooting')}</h2>
					{#if a.shot_games}
						<div class="rounded-xl bg-paper p-3">
							<p class="text-xs text-muted">{t('analytics.shots_fa')}</p>
							<p class="mt-1 font-mono text-xl font-semibold text-ink">{a.shots_for} <span class="text-dim">/</span> <span class="text-club-dark">{a.shots_against}</span></p>
						</div>
						<div class="grid grid-cols-2 gap-3">
							<div class="rounded-xl bg-paper p-3"><p class="text-xs text-muted">{t('analytics.shooting_pct')}</p><p class="mt-1 font-mono text-xl font-semibold text-ink">{pct(a.shooting_pct)}</p></div>
							<div class="rounded-xl bg-paper p-3"><p class="text-xs text-muted">{t('analytics.save_pct')}</p><p class="mt-1 font-mono text-xl font-semibold text-ink">{pct(a.save_pct)}</p></div>
						</div>
					{:else}
						<p class="text-sm text-muted">{t('analytics.no_shots')}</p>
					{/if}
					<div class="mt-auto grid grid-cols-2 gap-3">
						<div class="rounded-xl bg-paper p-3"><p class="text-xs text-muted">{t('analytics.gwg')}</p><p class="mt-1 font-mono text-xl font-semibold text-ink">{a.game_winning_goals}</p></div>
						<div class="rounded-xl bg-paper p-3"><p class="text-xs text-muted">{t('analytics.eng')}</p><p class="mt-1 font-mono text-xl font-semibold text-ink">{a.empty_net_goals}</p></div>
					</div>
				</section>
			</div>

			<!-- When goals happen. -->
			<section class="card card-body gap-4">
				<div class="flex flex-wrap items-start justify-between gap-3">
					<h2 class="card-title"><Goal size={18} aria-hidden="true" /> {t('analytics.when')}</h2>
					<div class="flex gap-4 text-sm text-muted">
						<span class="inline-flex items-center gap-2"><span class="h-3 w-3 rounded-sm bg-ink"></span>{t('analytics.goals_for')}</span>
						<span class="inline-flex items-center gap-2"><span class="h-3 w-3 rounded-sm bg-club"></span>{t('analytics.goals_against')}</span>
					</div>
				</div>
				{#if a.busiest_minutes}<p class="text-sm text-slate">{t('analytics.busiest', { range: minuteLabel(a.busiest_minutes), gf: a.busiest_minutes.goals_for, ga: a.busiest_minutes.goals_against })}</p>{/if}
				{#if hasSheets}
					<div class="grid gap-6 md:grid-cols-[1.4fr_1fr]">
						<div>
							<p class="font-mono text-[11px] tracking-[.14em] text-muted uppercase">{t('analytics.by_minute')}</p>
							<ForAgainstBars rows={a.goal_minutes} axisLabel={minuteAxis} tipLabel={minuteLabel} ariaLabel={t('analytics.by_minute')} />
						</div>
						<div>
							<p class="font-mono text-[11px] tracking-[.14em] text-muted uppercase">{t('analytics.by_period')}</p>
							{#if a.periods.length}
								<ForAgainstBars rows={a.periods} axisLabel={periodAxis} tipLabel={periodLabel} ariaLabel={t('analytics.by_period')} />
							{:else}
								<p class="py-6 text-center text-sm text-muted">{t('analytics.no_sheet_data')}</p>
							{/if}
						</div>
					</div>
				{:else}
					<p class="py-6 text-center text-sm text-muted">{t('analytics.no_sheet_data')}</p>
				{/if}
			</section>

			<!-- Home vs away. -->
			<div class="grid gap-4 md:grid-cols-2">
				{#each splits as { key, row, Icon } (key)}
					<section class="card card-body gap-3">
						<h2 class="card-title"><Icon size={18} aria-hidden="true" /> {t(`analytics.split.${key}`)} <span class="font-mono text-xs font-normal text-muted">· {row.games === 1 ? t('analytics.games_one') : t('analytics.games', { n: row.games })}</span></h2>
						{#if row.games}
							<div class="flex flex-wrap items-baseline gap-x-6 gap-y-2">
								<span class="font-display text-4xl leading-none font-extrabold text-ink">{row.won}–{row.drawn}–{row.lost}</span>
								<span class="font-mono text-sm text-slate">{t('analytics.goals_short', { gf: row.gf, ga: row.ga })} · {signed(row.diff)}</span>
								<span class="font-mono text-sm text-muted">{t('analytics.avg_short', { f: num(row.avg_for), a: num(row.avg_against) })}</span>
							</div>
							<div class="flex h-2 overflow-hidden rounded-full bg-club" title={t('analytics.share_for', { n: row.share_for })}><div class="h-full bg-ink" style="width: {row.share_for}%"></div></div>
						{:else}
							<p class="text-sm text-muted">{t('analytics.no_games_here')}</p>
						{/if}
					</section>
				{/each}
			</div>

			<!-- Leaderboards. -->
			<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
				<Leaderboard title={t('analytics.lead.points')} rows={a.top_points} unit={t('analytics.unit.points')} />
				<Leaderboard title={t('analytics.lead.goals')} rows={a.top_scorers} unit={t('analytics.unit.goals')} />
				<Leaderboard title={t('analytics.lead.assists')} rows={a.top_assists} unit={t('analytics.unit.assists')} />
				<Leaderboard title={t('analytics.lead.pim')} rows={a.top_offenders} unit={t('analytics.unit.pim')} />
			</div>

			<div class="grid gap-4 lg:grid-cols-2">
				<!-- Goalies. -->
				<section class="card">
					<div class="px-6 pt-5 pb-2"><h2 class="card-title"><ShieldHalf size={18} aria-hidden="true" /> {t('analytics.goalies')}</h2></div>
					{#if a.goalies.length || a.goalie_unattributed}
						<div class="overflow-x-auto">
							<table class="table">
								<thead><tr><th>{t('analytics.col.goalie')}</th><th class="text-right">{t('analytics.col.minutes')}</th><th class="text-right">{t('analytics.col.conceded')}</th><th class="text-right">{t('analytics.col.gaa')}</th></tr></thead>
								<tbody>
									{#each a.goalies as g, i (`${g.member_id ?? g.name}-${i}`)}
										<tr>
											<td>{#if g.number !== null}<span class="mr-1 font-mono text-xs text-muted">#{g.number}</span>{/if}{#if g.member_id}<a class="link font-semibold" href={memberHref(g.member_id)}>{g.name}</a>{:else}<span class="font-semibold">{g.name}</span>{/if}</td>
											<td class="text-right font-mono">{g.minutes ?? '—'}</td>
											<td class="text-right font-mono">{g.total}</td>
											<td class="text-right font-mono">{num(g.gaa, 2)}</td>
										</tr>
									{/each}
									{#if a.goalie_unattributed}
										<tr class="text-muted"><td>{t('analytics.no_goalie')}</td><td class="text-right font-mono">—</td><td class="text-right font-mono">{a.goalie_unattributed}</td><td class="text-right font-mono">—</td></tr>
									{/if}
								</tbody>
							</table>
						</div>
						<p class="px-6 pt-2 pb-5 text-xs text-muted">{t('analytics.goalies_help')}</p>
					{:else}
						<p class="px-6 pb-6 text-sm text-muted">{t('analytics.no_goalies')}</p>
					{/if}
				</section>

				<!-- Per opponent. -->
				<section class="card">
					<div class="px-6 pt-5 pb-2"><h2 class="card-title"><Trophy size={18} aria-hidden="true" /> {t('analytics.opponents')}</h2></div>
					<div class="overflow-x-auto">
						<table class="table">
							<thead><tr><th>{t('analytics.col.opponent')}</th><th class="text-right">{t('analytics.col.gp')}</th><th class="text-right">{t('analytics.col.wdl')}</th><th class="text-right">{t('analytics.col.goals')}</th></tr></thead>
							<tbody>
								{#each a.opponents as o (o.name)}
									<tr>
										<td class="font-semibold">{o.name || '—'}</td>
										<td class="text-right font-mono">{o.games}</td>
										<td class="text-right font-mono">{o.won}–{o.drawn}–{o.lost}</td>
										<td class="text-right font-mono">{o.gf}–{o.ga} <span class="text-muted">({signed(o.gf - o.ga)})</span></td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>
			</div>

			<!-- Game by game, newest first. -->
			<section class="card">
				<div class="flex flex-wrap items-baseline justify-between gap-3 px-6 pt-5 pb-2">
					<h2 class="card-title">{t('analytics.results')} <span class="font-mono text-xs font-normal text-muted">· {a.games}</span></h2>
				</div>
				<div class="overflow-x-auto">
					<table class="table min-w-[40rem]">
						<thead>
							<tr><th>{t('analytics.col.date')}</th><th>{t('analytics.col.opponent')}</th><th>{t('analytics.col.venue')}</th><th>{t('analytics.col.result')}</th><th class="w-1/4">{t('analytics.col.goals')}</th><th class="text-right">{t('analytics.col.pim')}</th></tr>
						</thead>
						<tbody>
							{#each a.game_rows as row (row.event.id)}
								<tr class="hover:bg-row-focus">
									<td class="font-mono text-sm whitespace-nowrap text-slate"><a class="link" href={eventHref(row.event.id)}>{formatEventDate(row.event.start)}</a></td>
									<td>
										<a class="link font-semibold" href={eventHref(row.event.id)}>{row.event.opponent ?? row.event.title}</a>
										{#if row.event.competition || row.event.is_friendly}<p class="text-xs text-muted">{[row.event.competition, row.event.is_friendly ? t('analytics.friendly') : ''].filter(Boolean).join(' · ')}</p>{/if}
									</td>
									<td class="text-sm text-slate">{row.event.is_home_game ? t('analytics.home') : t('analytics.away')}</td>
									<td>
										<span class="inline-flex items-center gap-2">
											<span class="inline-flex size-6 items-center justify-center rounded-md border font-mono text-xs font-bold {RESULT_CHIP[row.result]}" title={t(`analytics.result_long.${row.result}`)}>{resultLabel(row.result)}</span>
											<span class="font-display text-xl leading-none font-extrabold text-ink">{row.goals_for}–{row.goals_against}</span>
										</span>
									</td>
									<td>
										<div class="flex gap-1" style="width: {row.total_pct}%" aria-hidden="true">
											<span class="h-2 rounded-full bg-ink" style="flex: {row.goals_for} 1 0"></span>
											<span class="h-2 rounded-full bg-club" style="flex: {row.goals_against} 1 0"></span>
										</div>
									</td>
									<td class="text-right font-mono text-sm">{row.pim_for ?? '—'} / {row.pim_against ?? '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<p class="px-6 pt-2 pb-5 text-xs text-muted">{t('analytics.plus_minus')}</p>
			</section>
		{/if}
	</div>
{/if}
