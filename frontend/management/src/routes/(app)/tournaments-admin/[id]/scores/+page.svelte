<script lang="ts">
	import { Check, ClipboardPen, ListOrdered, Pencil, Plus, Trash2 } from '@lucide/svelte';
	import { api, type ApiCall } from '#lib/management-api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Toast from '#lib/components/Toast.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, numOrNull, Runner, TBASE, type MatchDoc, type MatchEvent, type MatchEventType } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const canOperate = $derived(tn.can_operate);
	const run = new Runner();

	const attrs = $derived(data.attributes ?? []);
	const manualAttrs = $derived(attrs.filter((a) => a.source === 'manual'));
	const timelineAttrs = $derived(attrs.filter((a) => a.source === 'timeline'));

	let open = $state(false);
	let m = $state<MatchDoc | null>(null);
	let f = $state({ home: '', away: '', ot: false, ph: '', pa: '', attrs: {} as Record<string, { home: string; away: string }> });
	function openResult(match: MatchDoc) {
		m = match;
		f = { home: match.score_home === null ? '' : String(match.score_home), away: match.score_away === null ? '' : String(match.score_away), ot: match.went_to_overtime, ph: match.penalty_home === null ? '' : String(match.penalty_home), pa: match.penalty_away === null ? '' : String(match.penalty_away), attrs: Object.fromEntries(manualAttrs.map((a) => [a.key, { home: String(match.attributes?.[a.key]?.home ?? 0), away: String(match.attributes?.[a.key]?.away ?? 0) }])) };
		run.clear();
		open = true;
	}
	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!m) return;
		const body = { score_home: Number(f.home), score_away: Number(f.away), went_to_overtime: f.ot, penalty_home: numOrNull(f.ph), penalty_away: numOrNull(f.pa), ...(manualAttrs.length ? { attributes: Object.fromEntries(manualAttrs.map((a) => [a.key, { home: Number(f.attrs[a.key]?.home || 0), away: Number(f.attrs[a.key]?.away || 0) }])) } : {}) };
		if (await run.post<{ updated_match_ids: string[] }>(`/tournaments/matches/${m.id}/result`, body, undefined, { notice: t('tn.scores.saved') })) open = false;
	}
	const nextRound = (stageId: string) => run.post<{ generated_count: number }>(`/tournaments/${tn.id}/stages/${stageId}/swiss/next-round`, {}, (d) => void (run.notice = t('tn.scores.generated', { n: d?.generated_count ?? 0 })), { notice: '' });
	const done = (x: MatchDoc) => x.score_home !== null && x.score_away !== null;

	// Game timeline
	const EV_TYPES: MatchEventType[] = ['goal', 'penalty', 'goalie_change', 'timeout', 'other'];
	let tlOpen = $state(false);
	let tlId = $state('');
	let events = $state<MatchEvent[]>([]);
	let ev = $state({ id: '', type: 'goal' as MatchEventType, side: 'home', minute: '', player: '', note: '', value: '' });
	const tlMatch = $derived(data.scores?.stages.flatMap((st) => st.rounds.flatMap((r) => r.matches)).find((x) => x.id === tlId) ?? null);
	const sorted = $derived([...events].sort((a, b) => (a.minute ?? 1e9) - (b.minute ?? 1e9)));
	const sideRequired = $derived(ev.type === 'goal' || ev.type === 'penalty');
	const valueLabel = $derived(ev.type === 'penalty' ? t('tn.scores.ev_value_penalty') : t('tn.scores.ev_value'));
	const evLabel = (type: string) => t(`tn.scores.ev_${type}`);
	const sideName = (e: MatchEvent) => (e.side === 'home' ? tlMatch?.home_display : e.side === 'away' ? tlMatch?.away_display : '') ?? '';
	const blankEv = () => (ev = { id: '', type: 'goal', side: 'home', minute: '', player: '', note: '', value: '' });
	/** Maps the 409 match_complete answer to a readable message. */
	async function tl<T>(call: () => Promise<ApiCall<T>>, onOk?: (d: T | undefined) => void | Promise<void>) {
		return run.run<T>(async () => {
			const res = await call();
			if (!res.ok && res.code === 'match_complete') res.message = t('tn.scores.match_complete');
			return res;
		}, onOk);
	}
	const loadEvents = async () => {
		const res = await api<{ events: MatchEvent[] }>('GET', `/tournaments/matches/${tlId}/events`);
		if (res.ok) events = res.data?.events ?? [];
		else run.error = res.message || t('common.error.generic');
	};
	async function openTimeline(match: MatchDoc) {
		tlId = match.id;
		events = [];
		blankEv();
		run.clear();
		tlOpen = true;
		await loadEvents();
	}
	function editEvent(e: MatchEvent) {
		ev = { id: e.id, type: e.type, side: e.side ?? 'home', minute: e.minute === null ? '' : String(e.minute), player: e.player, note: e.note, value: e.value === null ? '' : String(e.value) };
	}
	async function saveEvent(e: SubmitEvent) {
		e.preventDefault();
		const body = { type: ev.type, side: ev.side || null, minute: numOrNull(ev.minute), player: ev.player, note: ev.note, value: numOrNull(ev.value) };
		const call = () => (ev.id ? api('PATCH', `/tournaments/matches/events/${ev.id}`, { body }) : api('POST', `/tournaments/matches/${tlId}/events`, { body }));
		if (await tl(call, loadEvents)) blankEv();
	}
	const deleteEvent = (x: MatchEvent) => tl(() => api('DELETE', `/tournaments/matches/events/${x.id}`), async () => { if (ev.id === x.id) blankEv(); await loadEvents(); });

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.scores') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.scores'))}</title></svelte:head>

<div class="grid gap-4">
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
					<div class="grid gap-1">
						<h3 class="flex items-center gap-2 text-xs font-bold text-muted">{r.label}{#if r.all_complete}<span class="badge badge-success badge-xs">{t('tn.scores.complete')}</span>{/if}</h3>
						<div class="overflow-x-auto">
							<table class="table table-sm">
								<tbody>
									{#each r.matches as x (x.id)}
										<tr class="hover:bg-paper">
											<td class="w-36 font-mono text-xs whitespace-nowrap">{x.scheduled_start ? fmtDateTime(x.scheduled_start) : '—'}</td>
											<td class="hidden w-24 text-xs sm:table-cell">{x.field?.name ?? ''}</td>
											<td class="text-right text-sm">{x.home_display}</td>
											<td class="w-28 text-center font-mono text-sm font-bold whitespace-nowrap">
												{#if done(x)}{x.score_home} – {x.score_away}{#if x.went_to_overtime} <span class="text-xs font-normal text-muted">{t('tn.scores.ot')}</span>{/if}{#if x.penalty_home !== null} <span class="text-xs font-normal text-muted">({x.penalty_home}–{x.penalty_away})</span>{/if}{:else}<span class="text-muted">vs</span>{/if}{#each attrs as a (a.key)}{#if x.attributes?.[a.key] && (x.attributes[a.key].home || x.attributes[a.key].away)}<div class="text-xs font-normal text-muted" title={a.name}>{a.name} {x.attributes[a.key].home}–{x.attributes[a.key].away}</div>{/if}{/each}
											</td>
											<td class="text-sm">{x.away_display}</td>
											<td class="w-32 text-right"><div class="flex items-center justify-end gap-1">
												<button class="btn btn-outline btn-xs btn-square relative" type="button" aria-label={t('tn.scores.timeline')} title={t('tn.scores.timeline')} onclick={() => openTimeline(x)}><ListOrdered size={13} />{#if (x.event_count ?? 0) > 0}<span class="badge badge-neutral badge-xs absolute -top-1.5 -right-1.5">{x.event_count}</span>{/if}</button>
												{#if canOperate}<button class="btn btn-outline btn-xs" type="button" disabled={!x.home_team || !x.away_team} onclick={() => openResult(x)}>{done(x) ? t('common.edit') : t('tn.scores.enter')}</button>{/if}
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
		<div class="grid grid-cols-2 gap-3">
			<label class="form-control"><span class="label-text mb-1">{t('tn.scores.pen_home')}</span><input class="input input-bordered w-full" type="number" min="0" bind:value={f.ph} /></label>
			<label class="form-control"><span class="label-text mb-1">{t('tn.scores.pen_away')}</span><input class="input input-bordered w-full" type="number" min="0" bind:value={f.pa} /></label>
		</div>
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
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
<Modal bind:open={tlOpen} wide title={t('tn.scores.timeline_for')}>
	<div class="grid gap-3">
		{#if tlMatch}
			<p class="text-sm font-semibold">{tlMatch.home_display} {tlMatch.score_home ?? '–'} – {tlMatch.score_away ?? '–'} {tlMatch.away_display}</p>
		{/if}
		<p class="text-xs text-muted">{t('tn.scores.timeline_help')}</p>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<div class="overflow-x-auto">
			<table class="table table-sm">
				<tbody>
					{#each sorted as e (e.id)}
						<tr class="hover:bg-paper">
							<td class="w-12 font-mono text-xs">{e.minute !== null ? `${e.minute}'` : ''}</td>
							<td class="text-sm font-semibold whitespace-nowrap">{evLabel(e.type)}</td>
							<td class="text-sm">{sideName(e)}</td>
							<td class="text-sm">{e.player}</td>
							<td class="text-xs text-muted">{e.note}</td>
							<td class="text-right font-mono text-xs">{e.value ?? ''}</td>
							<td class="w-20 text-right">
								{#if canOperate}
									<div class="flex justify-end gap-1">
										<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.scores.ev_edit')} title={t('tn.scores.ev_edit')} onclick={() => editEvent(e)}><Pencil size={13} /></button>
										<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.scores.ev_delete')} title={t('tn.scores.ev_delete')} disabled={run.busy} onclick={() => deleteEvent(e)}><Trash2 size={13} /></button>
									</div>
								{/if}
							</td>
						</tr>
					{:else}
						<tr><td class="text-sm text-muted">{t('tn.scores.ev_empty')}</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if canOperate}
			<form class="grid gap-3 rounded-lg border border-line p-3" onsubmit={saveEvent}>
				<p class="text-xs font-bold text-muted">{ev.id ? t('tn.scores.ev_edit') : t('tn.scores.ev_add')}</p>
				<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.ev_type')}</span>
						<select class="select select-bordered w-full" bind:value={ev.type}>{#each EV_TYPES as ty (ty)}<option value={ty}>{evLabel(ty)}</option>{/each}</select></label>
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.ev_side')}</span>
						<select class="select select-bordered w-full" bind:value={ev.side} required={sideRequired}>
							{#if !sideRequired}<option value="">—</option>{/if}
							<option value="home">{tlMatch?.home_display ?? t('tn.scores.ev_home')}</option>
							<option value="away">{tlMatch?.away_display ?? t('tn.scores.ev_away')}</option>
						</select></label>
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.ev_minute')}</span><input class="input input-bordered w-full" type="number" min="0" bind:value={ev.minute} /></label>
					<label class="form-control"><span class="label-text mb-1">{valueLabel}</span><input class="input input-bordered w-full" type="number" min="0" bind:value={ev.value} /></label>
				</div>
				<div class="grid gap-3 sm:grid-cols-2">
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.ev_player')}</span><input class="input input-bordered w-full" type="text" bind:value={ev.player} /></label>
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.ev_note')}</span><input class="input input-bordered w-full" type="text" bind:value={ev.note} /></label>
				</div>
				{#each ['type', 'side', 'minute', 'value'] as k (k)}{#if run.fields[k]}<span class="text-xs text-error">{run.fields[k]}</span>{/if}{/each}
				<div class="flex justify-end gap-2">
					{#if ev.id}<button class="btn btn-outline" type="button" onclick={blankEv}>{t('common.cancel')}</button>{/if}
					<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('tn.scores.ev_save')}</button>
				</div>
			</form>
		{/if}
	</div>
</Modal>
<Toast message={run.notice} onhide={() => (run.notice = '')} />
