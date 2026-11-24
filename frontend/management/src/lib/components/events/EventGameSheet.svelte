<script lang="ts">
	import { Clock, Zap, Bug, CircleDot, CirclePause, LoaderCircle, ShieldUser, Target, Timer } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EventBackdrop from '#lib/components/portal/EventBackdrop.svelte';
	import EventGameRoster from '#lib/components/events/EventGameRoster.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { formatDateTime, type GameSheet, type GameSheetRow } from '#lib/events/types.ts';

	/** The game sheet: a scoreboard with the line score, a few totals, and a two-sided timeline (ours left, theirs right). */
	let { sheet, eventId, canDebug = false, ourLogo = '', theirLogo = '', background = null }: { sheet: GameSheet; eventId: string; canDebug?: boolean; ourLogo?: string; theirLogo?: string; background?: string | null } = $props();
	const lineup = $derived(sheet.lineup);

	// -- admins: what the live detection recorded, and whether it was pushed (for debugging) --
	type Moment = { id: string; key: string; kind: string; kind_label: string; description: string; game_seconds: number | null; detected_at: string; notifiable: boolean; delivered_at: string | null; state: 'pushed' | 'catch_up' | 'not_pushed' };
	let moments = $state<Moment[] | null>(null);
	let momentsOpen = $state(false);
	let momentsBusy = $state(false);
	let momentsError = $state('');
	async function toggleMoments() {
		momentsOpen = !momentsOpen;
		if (!momentsOpen || moments) return;
		momentsBusy = true;
		momentsError = '';
		const res = await api<Moment[]>('GET', `/events/${eventId}/game-moments`);
		momentsBusy = false;
		if (res.ok && res.data) moments = res.data;
		else momentsError = res.message || t('common.error.generic');
	}
	const clock = (s: number | null) => (s === null ? '–' : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`);
	const STATE_STYLE = { pushed: 'bg-ok-bg text-ok-text border-ok-border', catch_up: 'bg-subhead text-muted border-line', not_pushed: 'bg-warn-bg text-warn-text border-warn-border' } as const;
	const icons = { goal: CircleDot, timer: Timer, 'shield-user': ShieldUser, target: Target, 'circle-pause': CirclePause } as const;

	const rows = $derived(sheet.periods.flatMap((p) => p.rows));
	const count = (type: string, ours: boolean) => rows.filter((r) => r.type === type && r.ours === ours).length;
	const totals = $derived([
		{ key: 'goals', label: t('events.sheet.goals'), ours: count('goal', true), theirs: count('goal', false) },
		{ key: 'penalties', label: t('events.sheet.penalties'), ours: count('penalty', true), theirs: count('penalty', false) }
	]);
	const [scoreOurs, scoreTheirs] = $derived((sheet.score || '').split(/\s*[–-]\s*/).concat(['', '']).slice(0, 2)) as unknown as [string, string];

	const tone = (r: GameSheetRow) => (r.type === 'goal' ? (r.ours ? 'bg-club text-club-content' : 'bg-ink text-white') : r.serious ? 'bg-danger-bg text-danger' : 'bg-warn-bg text-warn-text');
</script>

<div class="grid gap-3">
	<!-- Scoreboard -->
	<section class="card relative isolate overflow-hidden border-ink bg-ink text-white">
		{#if background}<EventBackdrop src={background} />{/if}
		<!-- Each side's logo as a big, light grayscale watermark behind its name (transparency kept). -->
		{#if ourLogo}<img src={ourLogo} alt="" aria-hidden="true" class="pointer-events-none absolute top-1/2 -left-10 -z-10 h-[200%] w-auto max-w-none -translate-y-1/2 object-contain opacity-[0.3] [filter:grayscale(1)_brightness(1.7)]" />{/if}
		{#if theirLogo}<img src={theirLogo} alt="" aria-hidden="true" class="pointer-events-none absolute top-1/2 -right-6 -z-10 h-[150%] w-auto max-w-none -translate-y-1/2 object-contain opacity-[0.3] [filter:grayscale(1)_brightness(1.7)]" />{/if}
		<div class="flex flex-wrap items-center justify-between gap-2 px-6 pt-3">
			<span class="font-mono text-xs tracking-[.08em] text-on-dark uppercase">{t('events.tab.sheet')}</span>
			<div class="flex flex-wrap items-center gap-2">
				<span class="badge badge-sm {sheet.status === 'live' ? 'badge-error' : 'border-white/60 bg-transparent text-white'}">{sheet.status === 'live' ? t('events.live') : sheet.status === 'final' ? t('events.sheet.final') : t('events.sheet.not_started')}</span>
				{#if sheet.updated_at}<span class="font-mono text-xs text-on-dark">{t('events.sheet.updated', { when: formatDateTime(sheet.updated_at) })}</span>{/if}
			</div>
		</div>
		<div class="grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-6 py-3">
			<p class="truncate text-right font-display text-xl font-extrabold tracking-[.03em] uppercase sm:text-3xl">{sheet.ours_label}</p>
			<p class="font-display text-5xl leading-none font-extrabold tabular-nums sm:text-6xl">{scoreOurs || '–'}<span class="mx-2 text-white/40">:</span>{scoreTheirs || '–'}</p>
			<p class="truncate font-display text-xl font-extrabold tracking-[.03em] uppercase sm:text-3xl">{sheet.theirs_label}</p>
		</div>
		{#if sheet.period_scores.length}
			<ul class="flex flex-wrap justify-center gap-px border-t border-white/10 bg-white/10">
				{#each sheet.period_scores as p, i (i)}
					<li class="min-w-24 flex-1 bg-ink px-4 py-1.5 text-center">
						<span class="block font-mono text-[10px] tracking-[.1em] text-on-dark uppercase">{p.label}</span>
						<span class="font-display text-xl font-extrabold tabular-nums">{p.score}</span>
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<!-- Totals -->
	{#if rows.length}
		<div class="card flex flex-wrap items-center justify-center gap-x-8 gap-y-1 px-4 py-2">
			{#each totals as x (x.key)}
				<p class="flex items-baseline gap-2">
					<span class="font-display text-xl font-extrabold text-ink tabular-nums">{x.ours}</span>
					<span class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{x.label}</span>
					<span class="font-display text-xl font-extrabold text-muted tabular-nums">{x.theirs}</span>
				</p>
			{/each}
		</div>
	{/if}

	<!-- Two thirds: the timeline; one third: who dressed (switch between the two teams). -->
	<div class="grid items-start gap-3 {lineup ? 'lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]' : ''}">
		<div class="grid min-w-0 gap-3">
	{#if canDebug}
		<section class="card card-body gap-3">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<div>
					<h3 class="font-display text-base font-extrabold tracking-[.06em] text-ink uppercase flex items-center gap-2"><Zap size={16} class="shrink-0" aria-hidden="true" /> {t('events.sheet.moments')}</h3>
					<p class="text-xs text-muted">{t('events.sheet.moments_help')}</p>
				</div>
				<button class="btn btn-outline btn-sm gap-2" type="button" aria-expanded={momentsOpen} onclick={toggleMoments}>
					{#if momentsBusy}<LoaderCircle size={14} class="animate-spin" />{:else}<Bug size={14} />{/if}
					{momentsOpen ? t('events.sheet.moments_hide') : t('events.sheet.moments_show')}
				</button>
			</div>
			{#if momentsOpen}
				{#if momentsError}<Alert kind="error">{momentsError}</Alert>
				{:else if moments && !moments.length}<p class="text-sm text-muted">{t('events.sheet.moments_none')}</p>
				{:else if moments}
					<div class="overflow-x-auto">
						<table class="table table-sm">
							<thead class="bg-subhead">
								<tr><th>{t('events.sheet.m.clock')}</th><th>{t('events.sheet.m.kind')}</th><th>{t('events.sheet.m.what')}</th><th>{t('events.sheet.m.state')}</th><th>{t('events.sheet.m.detected')}</th></tr>
							</thead>
							<tbody>
								{#each moments as m (m.id)}
									<tr>
										<td class="font-mono text-xs whitespace-nowrap">{clock(m.game_seconds)}</td>
										<td class="text-sm whitespace-nowrap">{m.kind_label}</td>
										<td class="text-sm">{m.description}<span class="block font-mono text-[10px] text-dim" title={m.key}>{m.key}</span></td>
										<td><span class="badge badge-sm border {STATE_STYLE[m.state]}">{t(`events.sheet.state.${m.state}`)}</span>{#if m.delivered_at}<span class="block font-mono text-[10px] text-muted">{formatDateTime(m.delivered_at)}</span>{/if}</td>
										<td class="font-mono text-xs whitespace-nowrap text-muted">{formatDateTime(m.detected_at)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			{/if}
		</section>
	{/if}

	<!-- Timeline: one dense line per event -->
	<section class="card overflow-hidden">
		{#each sheet.periods as period, pi (pi)}
			<header class="flex items-center justify-between border-b border-line bg-subhead px-4 py-2 {period !== sheet.periods[0] ? 'border-t' : ''}">
				<h3 class="font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase flex items-center gap-2"><Clock size={16} class="shrink-0" aria-hidden="true" /> {period.title}</h3>
				{#if period.score}<span class="font-mono text-xs font-bold text-ink">{period.score}</span>{/if}
			</header>
			{#if period.rows.length}
				<ul class="divide-y divide-rule">
					{#each period.rows as r, i (i)}
						{@const Icon = icons[r.icon as keyof typeof icons] ?? CirclePause}
						<li class="grid grid-cols-[3rem_1.75rem_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2 text-sm">
							<span class="font-mono text-xs text-muted">{r.time}</span>
							<span class="grid size-7 place-items-center rounded-full {tone(r)}" title={r.kind}><Icon size={14} aria-hidden="true" /></span>
							<p class="min-w-0 truncate">
								<span class="font-semibold text-ink">{r.who}</span>
								{#if r.detail}<span class="text-muted"> · {r.detail}</span>{/if}
								{#if r.tag}<span class="badge badge-xs ml-1 border {r.serious ? 'border-danger-border bg-danger-bg text-danger' : 'border-line bg-subhead text-muted'}">{r.tag}</span>{/if}
							</p>
							<span class="flex items-center gap-2 justify-self-end">
								{#if r.team && r.team !== r.who}<span class="hidden font-display text-xs font-bold tracking-[.06em] uppercase sm:inline {r.ours ? 'text-ink' : 'text-muted'}">{r.team}</span>{/if}
								{#if r.score}<span class="rounded bg-ink px-1.5 font-mono text-[11px] font-bold text-white">{r.score}</span>{/if}
							</span>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="px-4 py-2 text-sm text-muted">{t('events.sheet.no_rows')}</p>
			{/if}
		{:else}
			<p class="px-4 py-3 text-sm text-muted">{t('events.sheet.empty')}</p>
		{/each}
	</section>
		</div>
		{#if lineup}<EventGameRoster {lineup} />{/if}
	</div>
</div>
