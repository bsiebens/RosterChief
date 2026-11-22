<script lang="ts">
	import { Check, ChevronDown, ChevronRight, ClipboardPen, ListOrdered, Pencil, Plus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import NoticeAlert from '#lib/components/NoticeAlert.svelte';
	import LiveBadge from '#lib/components/tournaments/LiveBadge.svelte';
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
	const currentRound = (st: { rounds: { round_number: number; all_complete: boolean }[] }) => st.rounds.find((r) => !r.all_complete)?.round_number; // none when the stage is finished: every round is folded
	const isRoundOpen = (stageId: string, st: { rounds: { round_number: number; all_complete: boolean }[] }, n: number) => roundOpen[`${stageId}:${n}`] ?? n === currentRound(st);

	// A back-and-forth tie is one game: its legs share one row and one dialog, with a switch between the legs.
	const allMatches = $derived(data.scores?.stages.flatMap((st) => st.rounds.flatMap((r) => r.matches)) ?? []);
	const byId = (id: string | null | undefined) => (id ? (allMatches.find((x) => x.id === id) ?? null) : null);
	/** The leg to enter or follow next: the first leg until it is played, then the second. */
	const activeLeg = (x: MatchDoc) => {
		const first = byId(x.first_leg_id);
		return first && first.status !== 'complete' ? first : x;
	};
	let drafts = $state<Record<string, typeof f>>({});

	let open = $state(false);
	let m = $state<MatchDoc | null>(null);
	let mStage = $state<{ allow_draws: boolean; penalty_shots: boolean } | null>(null);
	let f = $state({ home: '', away: '', ot: false, ph: '', pa: '', attrs: {} as Record<string, { home: string; away: string }> });
	function openResult(match: MatchDoc, stage: { allow_draws: boolean; penalty_shots: boolean }) {
		drafts = {};
		m = match;
		mStage = stage;
		f = { home: match.score_home === null ? '' : String(match.score_home), away: match.score_away === null ? '' : String(match.score_away), ot: match.went_to_overtime, ph: match.penalty_home === null ? '' : String(match.penalty_home), pa: match.penalty_away === null ? '' : String(match.penalty_away), attrs: Object.fromEntries(manualAttrs.map((a) => [a.key, { home: String(match.attributes?.[a.key]?.home ?? 0), away: String(match.attributes?.[a.key]?.away ?? 0) }])) };
		run.clear();
		open = true;
	}
	// A level score needs deciding when the stage does not allow draws: by penalty shots, or (without them) by playing on.
	// A back-and-forth tie is judged on both legs added up: the first leg may end level, the second needs a winner over the two games.
	const firstLeg = $derived(byId(m?.first_leg_id));
	/** The first leg's score as far as it is known: what was typed for it in this dialog, else what is saved. */
	const firstHome = $derived(firstLeg ? (drafts[firstLeg.id]?.home !== undefined && drafts[firstLeg.id].home !== '' ? Number(drafts[firstLeg.id].home) : (firstLeg.score_home ?? 0)) : 0);
	const firstAway = $derived(firstLeg ? (drafts[firstLeg.id]?.away !== undefined && drafts[firstLeg.id].away !== '' ? Number(drafts[firstLeg.id].away) : (firstLeg.score_away ?? 0)) : 0);
	const addedHome = $derived(Number(f.home || 0) + firstHome);
	const addedAway = $derived(Number(f.away || 0) + firstAway);
	/** The legs of the game that is open in the dialog, in playing order. */
	const tieLegs = $derived.by(() => {
		if (!m) return [] as MatchDoc[];
		const second = m.leg === 1 ? (allMatches.find((x) => x.first_leg_id === m!.id) ?? null) : m.leg === 2 ? m : null;
		const first = second ? byId(second.first_leg_id) : null;
		return first && second ? [first, second] : [];
	});
	const formOf = (x: MatchDoc) => ({ home: x.score_home === null ? '' : String(x.score_home), away: x.score_away === null ? '' : String(x.score_away), ot: x.went_to_overtime, ph: x.penalty_home === null ? '' : String(x.penalty_home), pa: x.penalty_away === null ? '' : String(x.penalty_away), attrs: Object.fromEntries(manualAttrs.map((a) => [a.key, { home: String(x.attributes?.[a.key]?.home ?? 0), away: String(x.attributes?.[a.key]?.away ?? 0) }])) });
	/** The total of both legs as typed so far (the open leg from the form, the other from its draft or what is saved). */
	function legTotal(key: 'score_home' | 'score_away') {
		const side = key === 'score_home' ? 'home' : 'away';
		return tieLegs.reduce((sum, leg) => {
			const form = leg.id === m?.id ? f : drafts[leg.id];
			return sum + (form && form[side] !== '' ? Number(form[side]) : (leg[key] ?? 0));
		}, 0);
	}
	function switchLeg(target: MatchDoc) {
		if (!m || target.id === m.id) return;
		drafts[m.id] = f;
		m = target;
		f = drafts[target.id] ?? formOf(target);
		run.clear();
	}
	const level = $derived(m?.leg === 1 ? false : f.home !== '' && f.away !== '' && addedHome === addedAway);
	const needsShots = $derived(level && !!mStage && !mStage.allow_draws && mStage.penalty_shots);
	const drawBlocked = $derived(level && !!mStage && !mStage.allow_draws && !mStage.penalty_shots);
	const bodyOf = (form: typeof f, leg: MatchDoc) => ({
		score_home: Number(form.home),
		score_away: Number(form.away),
		went_to_overtime: form.ot,
		penalty_home: leg.leg === 1 ? null : numOrNull(form.ph),
		penalty_away: leg.leg === 1 ? null : numOrNull(form.pa),
		...(manualAttrs.length ? { attributes: Object.fromEntries(manualAttrs.map((a) => [a.key, { home: Number(form.attrs[a.key]?.home || 0), away: Number(form.attrs[a.key]?.away || 0) }])) } : {})
	});
	/** Saves what is typed: the open leg, and the other leg of the game when it was filled in too (first leg first). */
	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!m) return;
		const current = m;
		const forms = new Map<string, typeof f>([[current.id, f]]);
		for (const leg of tieLegs) {
			const d = drafts[leg.id];
			if (leg.id !== current.id && d && d.home !== '' && d.away !== '') forms.set(leg.id, d);
		}
		const order = (tieLegs.length ? tieLegs : [current]).filter((leg) => forms.has(leg.id));
		for (const leg of order) {
			if (!(await run.post<{ updated_match_ids: string[] }>(`/tournaments/matches/${leg.id}/result`, bodyOf(forms.get(leg.id)!, leg), undefined, { notice: t('tn.scores.saved'), reload: leg === order[order.length - 1] }))) {
				if (leg.id !== current.id) switchLeg(leg); // show the leg that has the problem
				return;
			}
		}
		open = false;
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
									{#each r.matches.filter((y) => y.leg !== 1) as x (x.id)}
										{@const first = byId(x.first_leg_id)}
										{@const act = activeLeg(x)}
										{@const tHome = first ? (first.score_home ?? 0) + (x.score_home ?? 0) : x.score_home}
										{@const tAway = first ? (first.score_away ?? 0) + (x.score_away ?? 0) : x.score_away}
										{@const shown = first ? done(first) || done(x) || x.status === 'in_progress' || first.status === 'in_progress' : done(x) || x.status === 'in_progress'}
										<tr class="hover:bg-paper">
											<td class="w-36 font-mono text-xs whitespace-nowrap">{#if first}<div>{first.scheduled_start ? fmtDateTime(first.scheduled_start) : '—'}</div><div class="text-muted">{x.scheduled_start ? fmtDateTime(x.scheduled_start) : '—'}</div>{:else}{x.scheduled_start ? fmtDateTime(x.scheduled_start) : '—'}{/if}</td>
											<td class="hidden text-xs whitespace-nowrap sm:table-cell">{x.field?.name ?? ''}</td>
											<td class="text-right text-sm">{x.home_display}</td>
											<td class="w-28 text-center font-mono text-sm font-bold whitespace-nowrap">
												{#if x.status === 'in_progress' || first?.status === 'in_progress'}<div class="mb-0.5"><LiveBadge /></div>{/if}{#if shown}{tHome ?? 0} – {tAway ?? 0}{#if x.went_to_overtime} <span class="text-xs font-normal text-muted">{t('tn.scores.ot')}</span>{/if}{#if x.penalty_home !== null} <span class="text-xs font-normal text-muted">({x.penalty_home}–{x.penalty_away})</span>{/if}{:else}<span class="text-muted">vs</span>{/if}{#if first}<div class="text-[10px] font-normal text-muted">{t('tn.legs_line', { a: `${first.score_home ?? '–'}:${first.score_away ?? '–'}`, b: `${x.score_home ?? '–'}:${x.score_away ?? '–'}` })}</div>{/if}{#each attrs as a (a.key)}{#if x.attributes?.[a.key] && (x.attributes[a.key].home || x.attributes[a.key].away)}<div class="text-xs font-normal text-muted" title={a.name}>{a.name} {x.attributes[a.key].home}–{x.attributes[a.key].away}</div>{/if}{/each}
											</td>
											<td class="text-sm">{x.away_display}</td>
											<td class="text-right"><div class="flex items-center justify-end gap-1">
												<a class="btn btn-outline btn-xs w-28 justify-center gap-1" href="{TBASE}/{tn.id}/matches/{act.id}" title={t('tn.scores.timeline')}><ListOrdered size={13} aria-hidden="true" /> {t('tn.scores.timeline')}{#if (x.event_count ?? 0) + (first?.event_count ?? 0) > 0}<span class="badge badge-neutral badge-xs">{(x.event_count ?? 0) + (first?.event_count ?? 0)}</span>{/if}</a>
												{#if canOperate}<button class="btn btn-outline btn-xs w-24 justify-center gap-1" type="button" disabled={!x.home_team || !x.away_team} onclick={() => openResult(act, st.stage)}>{#if done(x)}<Pencil size={13} aria-hidden="true" /> {t('common.edit')}{:else}<ClipboardPen size={13} aria-hidden="true" /> {t('tn.scores.enter')}{/if}</button>{/if}
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
		{#if tieLegs.length}
			<div class="flex flex-wrap items-center gap-2" role="group" aria-label={t('tn.scores.legs')}>
				{#each tieLegs as leg, i (leg.id)}
					<button type="button" aria-pressed={m?.id === leg.id} onclick={() => switchLeg(leg)} class="min-w-24 cursor-pointer rounded-lg border-2 px-4 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {m?.id === leg.id ? 'border-club bg-club/10 text-ink' : 'border-line bg-white text-muted hover:text-ink'}">{t('tn.leg', { n: i + 1 })}{#if leg.status === 'complete' || (drafts[leg.id]?.home ?? '') !== ''}<Check size={12} class="ml-1 inline" aria-hidden="true" />{/if}</button>
				{/each}
				<span class="text-xs text-muted">{t('tn.aggregate', { h: legTotal('score_home'), a: legTotal('score_away') })}</span>
			</div>
		{/if}
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
