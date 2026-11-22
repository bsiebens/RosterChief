<script lang="ts">
	import { ArrowLeftRight, ChevronLeft, Clock, MapPin, Siren, Target, Timer, MessageSquare } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import LiveBadge from '#lib/components/tournaments/LiveBadge.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { dateTimeLabel, tieWinner, ties, type GameEvent, type PublicMatch } from '#lib/tournaments-public.ts';

	let { data } = $props();
	const g = $derived(data.game);
	const path = $derived(g.tournament.path);
	const periods = $derived(g.periods);
	const tie = $derived(ties(periods.map((p) => p.match as PublicMatch))[0]);
	const m = $derived(tie.m);
	const winner = $derived(tieWinner(tie));
	const shown = $derived(tie.done || tie.live);
	const multi = $derived(periods.length > 1);
	let pick = $state(0); // 0 = the whole game, n = period n
	const rows = $derived(
		periods.flatMap((p, i) => (pick === 0 || pick === i + 1 ? p.events.map((e) => ({ e, period: i + 1, match: p.match })) : []))
	);
	const clock = (e: GameEvent) => (e.minute === null ? '' : `${String(e.minute).padStart(2, '0')}:${String(e.second ?? 0).padStart(2, '0')}`);
	const who = (e: GameEvent) => `${e.player_number !== null ? `#${e.player_number} ` : ''}${e.player}`.trim();
	const sideName = (e: GameEvent, match: PublicMatch) => (e.side === 'home' ? match.home_team?.name ?? match.home_display : e.side === 'away' ? match.away_team?.name ?? match.away_display : '');
	const icon = { goal: Target, penalty: Siren, goalie_change: ArrowLeftRight, timeout: Timer, other: MessageSquare } as const;
	const tone = { goal: 'bg-emerald-100 text-emerald-700', penalty: 'bg-amber-100 text-amber-700', goalie_change: 'bg-sky-100 text-sky-700', timeout: 'bg-slate-100 text-slate-600', other: 'bg-slate-100 text-slate-600' } as const;
</script>

<svelte:head><title>{pageTitle(`${m.home_display} – ${m.away_display} · ${g.tournament.name}`)}</title></svelte:head>

<a class="link inline-flex items-center gap-1 text-sm" href="/tournaments/{path}/schedule"><ChevronLeft size={14} aria-hidden="true" /> {t('tp.nav.schedule')}</a>

<section class="overflow-hidden rounded-2xl border bg-white shadow-sm {tie.live ? 'border-danger/50 ring-1 ring-danger/30' : 'border-line'}">
	<div class="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-line bg-subhead px-5 py-2.5 font-mono text-xs text-muted">
		<span>{m.round_label}{#if m.group} · {m.group.name}{/if}</span>
		{#if tie.start}<span class="flex items-center gap-1.5"><Clock size={12} aria-hidden="true" />{dateTimeLabel(tie.start)}</span>{/if}
		{#if m.field}<span class="flex items-center gap-1.5"><MapPin size={12} aria-hidden="true" />{m.field.name}</span>{/if}
		<span class="ml-auto">{#if tie.live}<LiveBadge size="lg" />{:else if tie.done}{t('ts.full_time')}{:else}{t('ts.not_started')}{/if}</span>
	</div>
	<div class="grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 py-8 sm:gap-8 sm:px-10 sm:py-10">
		{#each [{ team: m.home_team, name: m.home_display, win: winner === 'home' }, null, { team: m.away_team, name: m.away_display, win: winner === 'away' }] as side, i (i)}
			{#if side}
				<svelte:element this={side.team ? 'a' : 'div'} href={side.team ? `/tournaments/${path}/teams/${side.team.slug}` : undefined} class="flex min-w-0 flex-col items-center gap-3 text-center">
					{#if side.team}<ClubCrest name={side.team.name} logoUrl={side.team.logo_url} bg={side.team.primary_color || undefined} size={84} />{:else}<span class="flex size-[84px] items-center justify-center rounded-full border border-dashed border-line text-2xl text-muted">?</span>{/if}
					<span class="w-full truncate font-display text-lg font-extrabold uppercase sm:text-2xl {side.win ? 'text-ink' : 'text-ink/80'}">{side.team?.name ?? side.name}</span>
				</svelte:element>
			{:else}
				<div class="text-center">
					<div class="rounded-2xl bg-ink px-5 py-3 font-display text-4xl font-extrabold text-white tabular-nums sm:px-8 sm:text-6xl">{shown ? `${tie.home ?? 0} : ${tie.away ?? 0}` : t('tp.vs')}</div>
					{#if multi && shown}
						<p class="mt-2 flex justify-center gap-4 font-mono text-xs text-muted">
							{#each periods as p, n (p.match.id)}<span>P{n + 1} {p.match.score_home ?? '–'}:{p.match.score_away ?? '–'}</span>{/each}
						</p>
					{/if}
					{#if tie.done && m.penalty_home !== null && m.penalty_away !== null}<p class="mt-1 font-mono text-xs text-muted">{t('tp.penalties')} {m.penalty_home}–{m.penalty_away}</p>
					{:else if tie.done && m.went_to_overtime}<p class="mt-1 font-mono text-xs text-muted">{t('tp.overtime')}</p>{/if}
				</div>
			{/if}
		{/each}
	</div>
</section>

<section class="grid gap-4">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h2 class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{t('ts.timeline')}</h2>
		{#if multi}
			<div class="flex gap-2" role="group" aria-label={t('ts.periods')}>
				{#each [0, ...periods.map((_, n) => n + 1)] as n (n)}
					<button type="button" aria-pressed={pick === n} onclick={() => (pick = n)} class="rounded-full border px-4 py-1.5 font-display text-xs font-extrabold tracking-[.08em] uppercase {pick === n ? 'border-ink bg-ink text-white' : 'border-line bg-white text-muted hover:text-ink'}">{n === 0 ? t('ts.whole_game') : t('tn.leg', { n })}</button>
				{/each}
			</div>
		{/if}
	</div>

	{#if !rows.length}
		<div class="rounded-2xl border border-line bg-white py-10 text-center text-muted" role="status">{t('ts.no_events')}</div>
	{:else}
		<ol class="grid gap-2">
			{#each rows as { e, period, match } (e.id)}
				{@const Icon = icon[e.type] ?? MessageSquare}
				<li class="grid grid-cols-[auto_auto_1fr] items-center gap-3 rounded-xl border border-line bg-white px-4 py-3 shadow-sm {e.side === 'away' ? 'sm:ml-12' : e.side === 'home' ? 'sm:mr-12' : ''}">
					<span class="w-16 font-mono text-xs text-muted">{#if multi && pick === 0}P{period} {/if}{clock(e)}</span>
					<span class="flex size-9 items-center justify-center rounded-full {tone[e.type] ?? tone.other}"><Icon size={17} aria-hidden="true" /></span>
					<div class="min-w-0 text-sm">
						{#if e.type === 'goal'}
							<p class="font-semibold text-ink">{t('ts.ev.goal')} · {sideName(e, match)}</p>
							<p class="text-muted">{who(e) || t('tn.game.unknown_player')}{#if e.assists.length} <span class="font-mono text-xs">({t('tn.game.assists_short')}: {e.assists.map((a) => `${a.number !== null ? `#${a.number} ` : ''}${a.name}`).join(', ')})</span>{/if}</p>
						{:else if e.type === 'penalty'}
							<p class="font-semibold text-ink">{t('ts.ev.penalty')} · {sideName(e, match)}</p>
							<p class="text-muted">{who(e)}{#if e.penalty_name || e.penalty_code} — {e.penalty_name || e.penalty_code}{/if}{#if e.penalty_length} <span class="font-mono text-xs">({e.penalty_length})</span>{/if}</p>
						{:else if e.type === 'goalie_change'}
							<p class="font-semibold text-ink">{t('ts.ev.goalie_change')} · {sideName(e, match)}</p>
						{:else if e.type === 'timeout'}
							<p class="font-semibold text-ink">{t('ts.ev.timeout')} · {sideName(e, match)}</p>
						{:else}
							<p class="font-semibold text-ink">{e.note || t('ts.ev.other')}</p>
						{/if}
						{#if e.note && e.type !== 'other'}<p class="text-xs text-muted italic">{e.note}</p>{/if}
					</div>
				</li>
			{/each}
		</ol>
	{/if}
</section>
