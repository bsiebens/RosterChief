<script lang="ts">
	import { Check, ChevronDown, ChevronRight, ClipboardPen, ListOrdered, Pencil, Plus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import NoticeAlert from '#lib/components/NoticeAlert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, numOrNull, Runner, TBASE, type MatchDoc } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const canOperate = $derived(tn.can_operate);
	const run = new Runner();

	const attrs = $derived(data.attributes ?? []);
	const manualAttrs = $derived(attrs.filter((a) => a.source === 'manual'));
	const timelineAttrs = $derived(attrs.filter((a) => a.source === 'timeline'));

	// Rounds fold away; only the round being played (the first one not finished) starts open, the rest can be opened by hand.
	let roundOpen = $state<Record<string, boolean>>({});
	const currentRound = (st: { rounds: { round_number: number; all_complete: boolean }[] }) => (st.rounds.find((r) => !r.all_complete) ?? st.rounds[st.rounds.length - 1])?.round_number;
	const isRoundOpen = (stageId: string, st: { rounds: { round_number: number; all_complete: boolean }[] }, n: number) => roundOpen[`${stageId}:${n}`] ?? n === currentRound(st);

	let open = $state(false);
	let m = $state<MatchDoc | null>(null);
	let mStage = $state<{ allow_draws: boolean; penalty_shots: boolean } | null>(null);
	let f = $state({ home: '', away: '', ot: false, ph: '', pa: '', attrs: {} as Record<string, { home: string; away: string }> });
	function openResult(match: MatchDoc, stage: { allow_draws: boolean; penalty_shots: boolean }) {
		m = match;
		mStage = stage;
		f = { home: match.score_home === null ? '' : String(match.score_home), away: match.score_away === null ? '' : String(match.score_away), ot: match.went_to_overtime, ph: match.penalty_home === null ? '' : String(match.penalty_home), pa: match.penalty_away === null ? '' : String(match.penalty_away), attrs: Object.fromEntries(manualAttrs.map((a) => [a.key, { home: String(match.attributes?.[a.key]?.home ?? 0), away: String(match.attributes?.[a.key]?.away ?? 0) }])) };
		run.clear();
		open = true;
	}
	// A level score needs deciding when the stage does not allow draws: by penalty shots, or (without them) by playing on.
	// A back-and-forth tie is judged on both legs added up: the first leg may end level, the second needs a winner over the two games.
	const firstLeg = $derived(m?.first_leg_id ? (data.scores?.stages.flatMap((st) => st.rounds.flatMap((r) => r.matches)).find((x) => x.id === m!.first_leg_id) ?? null) : null);
	const addedHome = $derived(Number(f.home || 0) + (firstLeg?.score_home ?? 0));
	const addedAway = $derived(Number(f.away || 0) + (firstLeg?.score_away ?? 0));
	const level = $derived(m?.leg === 1 ? false : f.home !== '' && f.away !== '' && addedHome === addedAway);
	const needsShots = $derived(level && !!mStage && !mStage.allow_draws && mStage.penalty_shots);
	const drawBlocked = $derived(level && !!mStage && !mStage.allow_draws && !mStage.penalty_shots);
	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!m) return;
		const body = { score_home: Number(f.home), score_away: Number(f.away), went_to_overtime: f.ot, penalty_home: numOrNull(f.ph), penalty_away: numOrNull(f.pa), ...(manualAttrs.length ? { attributes: Object.fromEntries(manualAttrs.map((a) => [a.key, { home: Number(f.attrs[a.key]?.home || 0), away: Number(f.attrs[a.key]?.away || 0) }])) } : {}) };
		if (await run.post<{ updated_match_ids: string[] }>(`/tournaments/matches/${m.id}/result`, body, undefined, { notice: t('tn.scores.saved') })) open = false;
	}
	const nextRound = (stageId: string) => run.post<{ generated_count: number }>(`/tournaments/${tn.id}/stages/${stageId}/swiss/next-round`, {}, (d) => void (run.notice = t('tn.scores.generated', { n: d?.generated_count ?? 0 })), { notice: '' });
	const done = (x: MatchDoc) => x.score_home !== null && x.score_away !== null;

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.scores') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.scores'))}</title></svelte:head>

<div class="grid gap-4">
	<NoticeAlert message={run.notice} onhide={() => (run.notice = '')} />
	{#if run.error && !open}<Alert kind="error">{run.error}</Alert>{/if}
	{#if data.error || !data.scores}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#each data.scores.stages as st (st.stage.id)}
			<section class="card card-body gap-3">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ClipboardPen size={16} class="shrink-0" aria-hidden="true" /> {st.stage.name}</h2>
					<div class="flex flex-wrap gap-2">
						{#if st.ready_for_next_phase}<a class="btn btn-primary btn-sm gap-1" href="{TBASE}/{tn.id}/stages/{st.stage.id}/next-phase"><Check size={14} /> {t('tn.scores.review_next')}</a>{/if}
						{#if canOperate && st.swiss_ready_for_next_round}<button class="btn btn-primary btn-sm gap-1" type="button" disabled={run.busy} onclick={() => nextRound(st.stage.id)}><Plus size={14} /> {t('tn.scores.swiss_next')}</button>{/if}
					</div>
				</div>
				{#each st.rounds as r (r.round_number)}
					{@const expanded = isRoundOpen(st.stage.id, st, r.round_number)}
					<div class="grid gap-1">
						<h3 class="text-xs font-bold text-muted">
							<button class="flex w-full cursor-pointer items-center gap-2 py-1 text-left" type="button" aria-expanded={expanded} onclick={() => (roundOpen[`${st.stage.id}:${r.round_number}`] = !expanded)}>
								{#if expanded}<ChevronDown size={14} aria-hidden="true" />{:else}<ChevronRight size={14} aria-hidden="true" />{/if}
								{r.label}
								<span class="font-normal">({r.matches.length})</span>
								{#if r.all_complete}<span class="badge badge-success badge-xs">{t('tn.scores.complete')}</span>{/if}
							</button>
						</h3>
						<div class="overflow-x-auto" class:hidden={!expanded}>
							<table class="table table-sm">
								<tbody>
									{#each r.matches as x (x.id)}
										<tr class="hover:bg-paper">
											<td class="w-36 font-mono text-xs whitespace-nowrap">{x.scheduled_start ? fmtDateTime(x.scheduled_start) : '—'}</td>
											<td class="hidden text-xs whitespace-nowrap sm:table-cell">{x.field?.name ?? ''}</td>
											<td class="text-right text-sm">{x.home_display}</td>
											<td class="w-28 text-center font-mono text-sm font-bold whitespace-nowrap">
												{#if done(x)}{x.score_home} – {x.score_away}{#if x.went_to_overtime} <span class="text-xs font-normal text-muted">{t('tn.scores.ot')}</span>{/if}{#if x.penalty_home !== null} <span class="text-xs font-normal text-muted">({x.penalty_home}–{x.penalty_away})</span>{/if}{:else}<span class="text-muted">vs</span>{/if}{#if x.leg}<div class="text-[10px] font-normal text-muted">{t('tn.leg', { n: x.leg })}{#if x.aggregate} · {t('tn.aggregate', { h: x.aggregate[0], a: x.aggregate[1] })}{/if}</div>{/if}{#each attrs as a (a.key)}{#if x.attributes?.[a.key] && (x.attributes[a.key].home || x.attributes[a.key].away)}<div class="text-xs font-normal text-muted" title={a.name}>{a.name} {x.attributes[a.key].home}–{x.attributes[a.key].away}</div>{/if}{/each}
											</td>
											<td class="text-sm">{x.away_display}</td>
											<td class="text-right"><div class="flex items-center justify-end gap-1">
												<a class="btn btn-outline btn-xs relative gap-1" href="{TBASE}/{tn.id}/matches/{x.id}" title={t('tn.scores.timeline')}><ListOrdered size={13} aria-hidden="true" /> {t('tn.scores.timeline')}{#if (x.event_count ?? 0) > 0}<span class="badge badge-neutral badge-xs absolute -top-1.5 -right-1.5">{x.event_count}</span>{/if}</a>
												{#if canOperate}<button class="btn btn-outline btn-xs gap-1" type="button" disabled={!x.home_team || !x.away_team} onclick={() => openResult(x, st.stage)}>{#if done(x)}<Pencil size={13} aria-hidden="true" /> {t('common.edit')}{:else}<ClipboardPen size={13} aria-hidden="true" /> {t('tn.scores.enter')}{/if}</button>{/if}
											</div></td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</div>
				{:else}
					<p class="text-sm text-muted">{t('tn.sched.no_matches')}</p>
				{/each}
			</section>
		{:else}
			<div class="card card-body items-center py-10 text-center text-muted"><p>{t('tn.scores.empty')}</p></div>
		{/each}
	{/if}
</div>

<Modal bind:open title={t('tn.scores.enter')}>
	<form class="grid gap-3" onsubmit={save}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p class="text-sm font-semibold">{m?.home_display} – {m?.away_display}</p>
		<div class="grid grid-cols-2 gap-3">
			<label class="form-control"><span class="label-text mb-1">{m?.home_display}</span><input class="input input-bordered w-full" type="number" min="0" required bind:value={f.home} /></label>
			<label class="form-control"><span class="label-text mb-1">{m?.away_display}</span><input class="input input-bordered w-full" type="number" min="0" required bind:value={f.away} /></label>
		</div>
		{#each ['score_home', 'score_away'] as k (k)}{#if run.fields[k]}<span class="text-xs text-error">{run.fields[k]}</span>{/if}{/each}
		<label class="flex cursor-pointer items-center gap-3 rounded-lg border border-line p-3"><input type="checkbox" class="checkbox" bind:checked={f.ot} /><span class="text-sm">{t('tn.scores.overtime')}</span></label>
		{#if firstLeg}
			<p class="rounded-lg bg-paper p-3 text-sm text-muted">{t('tn.scores.first_leg', { h: firstLeg.score_home ?? '–', a: firstLeg.score_away ?? '–' })} · <strong class="text-ink">{t('tn.aggregate', { h: addedHome, a: addedAway })}</strong></p>
		{/if}
		{#if m?.leg === 1}
			<!-- a first leg may end level: no shootout -->
		{:else if drawBlocked}
			<Alert kind="warning">{t('tn.scores.draw_blocked')}</Alert>
		{:else if needsShots}
			<div class="grid gap-2 rounded-lg border border-line p-3">
				<p class="text-sm font-semibold text-ink">{t('tn.scores.shots_title')}</p>
				<p class="text-xs text-muted">{t('tn.scores.shots_help')}</p>
				<div class="grid grid-cols-2 gap-3">
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.pen_home')}</span><input class="input input-bordered w-full" type="number" min="0" required bind:value={f.ph} /></label>
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.pen_away')}</span><input class="input input-bordered w-full" type="number" min="0" required bind:value={f.pa} /></label>
				</div>
				{#each ['penalty_home', 'penalty_away'] as k (k)}{#if run.fields[k]}<span class="text-xs text-error">{run.fields[k]}</span>{/if}{/each}
			</div>
		{/if}
		{#if manualAttrs.length}
			<div class="grid gap-2 rounded-lg border border-line p-3">
				<p class="text-xs font-bold text-muted">{t('tn.scores.attr_manual')}</p>
				{#each manualAttrs as a (a.key)}
					<div class="grid grid-cols-[1fr_6rem_6rem] items-center gap-2">
						<span class="text-sm">{a.name}</span>
						<input class="input input-bordered input-sm w-full" type="number" min="0" aria-label="{a.name}: {m?.home_display}" bind:value={f.attrs[a.key].home} />
						<input class="input input-bordered input-sm w-full" type="number" min="0" aria-label="{a.name}: {m?.away_display}" bind:value={f.attrs[a.key].away} />
					</div>
				{/each}
			</div>
		{/if}
		{#if timelineAttrs.length && m}
			<div class="grid gap-1 rounded-lg border border-line p-3">
				<p class="text-xs font-bold text-muted">{t('tn.scores.attr_computed')}</p>
				{#each timelineAttrs as a (a.key)}
					<div class="flex justify-between text-sm"><span>{a.name}</span><span class="font-mono">{m.attributes?.[a.key]?.home ?? 0} – {m.attributes?.[a.key]?.away ?? 0}</span></div>
				{/each}
			</div>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy || drawBlocked}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
