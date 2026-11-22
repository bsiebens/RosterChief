<script lang="ts">
	import { ArrowDown, ArrowUp, CalendarClock, Eraser, GripVertical, Pencil, Plus, Save, Trash2, UtensilsCrossed, Wand2, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import NoticeAlert from '#lib/components/NoticeAlert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import ScheduleBoard from '#lib/components/tournaments/ScheduleBoard.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDay, fmtDateTime, fmtTime, fromLocalInput, numOrNull, Runner, strOrNull, toLocalInput, TBASE, type LunchDoc, type LunchResult, type MatchDoc, type ScheduleBlock } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const sched = $derived(data.schedule);
	const canOperate = $derived(tn.can_operate);
	const base = $derived(`/tournaments/${tn.id}`);
	const run = new Runner();
	const needsRegen = $derived(sched?.tournament.schedule_needs_regeneration ?? tn.schedule_needs_regeneration);
	let view = $state<'list' | 'board'>('board');

	// --- generate / clear
	let duration = $state('');
	let clearOpen = $state(false);
	const generate = () => run.post<{ scheduled_count: number }>(`${base}/schedule/generate`, duration ? { match_duration_minutes: numOrNull(duration) } : {}, (d) => void (run.notice = t('tn.sched.generated', { n: d?.scheduled_count ?? 0 })), { notice: '' });
	async function clear() {
		if (await run.post<{ cleared_count: number }>(`${base}/schedule/clear`, {}, (d) => void (run.notice = t('tn.sched.cleared', { n: d?.cleared_count ?? 0 })), { notice: '' })) clearOpen = false;
	}

	// --- hand reschedule (also used after a drop)
	let moveOpen = $state(false);
	let moving = $state<MatchDoc | null>(null);
	let moveField = $state('');
	let moveStart = $state('');
	function openMove(m: MatchDoc) {
		moving = m;
		moveField = m.field?.id ?? sched?.fields[0]?.id ?? '';
		moveStart = toLocalInput(m.scheduled_start);
		run.clear();
		moveOpen = true;
	}
	async function saveMove(e: SubmitEvent) {
		e.preventDefault();
		if (moving && (await run.post(`/tournaments/matches/${moving.id}/reschedule`, { field_id: moveField, scheduled_start: fromLocalInput(moveStart) }))) moveOpen = false;
	}
	// --- list view: the times of a round stay where they are, the games change places
	let dragMatch = $state<string | null>(null);
	let overMatch = $state<string | null>(null);
	/** A round in time order; games without a time go last. */
	const sortedRound = (ms: MatchDoc[]) => [...ms].sort((x, y) => (x.scheduled_start ?? '9').localeCompare(y.scheduled_start ?? '9') || x.id.localeCompare(y.id));
	const reorder = (ids: string[]) => run.post(`${base}/schedule/reorder`, { match_ids: ids });
	function moveRow(rows: MatchDoc[], fromId: string, toId: string) {
		dragMatch = overMatch = null;
		const ids = rows.filter((x) => x.scheduled_start).map((x) => x.id);
		const from = ids.indexOf(fromId);
		const to = ids.indexOf(toId);
		if (from < 0 || to < 0 || from === to) return;
		ids.splice(to, 0, ids.splice(from, 1)[0]);
		return reorder(ids);
	}
	function stepRow(rows: MatchDoc[], index: number, by: -1 | 1) {
		const ids = rows.filter((x) => x.scheduled_start).map((x) => x.id);
		const to = index + by;
		if (to < 0 || to >= ids.length) return;
		[ids[index], ids[to]] = [ids[to], ids[index]];
		return reorder(ids);
	}
	const drop = (m: MatchDoc, field_id: string, scheduled_start: string) => run.post(`/tournaments/matches/${m.id}/reschedule`, { field_id, scheduled_start });

	// --- blocks
	let blockOpen = $state(false);
	let editBlock = $state<ScheduleBlock | null>(null);
	let bf = $state({ title: '', starts_at: '', ends_at: '', field_id: '', team_ids: [] as string[] });
	let delBlock = $state<ScheduleBlock | null>(null);
	function openBlock(b: ScheduleBlock | null) {
		editBlock = b;
		bf = b ? { title: b.title, starts_at: toLocalInput(b.starts_at), ends_at: toLocalInput(b.ends_at), field_id: b.field?.id ?? '', team_ids: b.teams.map((x) => x.id) } : { title: '', starts_at: '', ends_at: '', field_id: '', team_ids: [] };
		run.clear();
		blockOpen = true;
	}
	async function saveBlock(e: SubmitEvent) {
		e.preventDefault();
		const body = { title: bf.title.trim(), starts_at: fromLocalInput(bf.starts_at), ends_at: fromLocalInput(bf.ends_at), field_id: strOrNull(bf.field_id), team_ids: bf.team_ids };
		if (await (editBlock ? run.patch(`${base}/schedule-blocks/${editBlock.id}`, body) : run.post(`${base}/schedule-blocks`, body))) blockOpen = false;
	}
	const toggleTeam = (id: string) => (bf.team_ids = bf.team_ids.includes(id) ? bf.team_ids.filter((x) => x !== id) : [...bf.team_ids, id]);
	let delOpen = $state(false);
	async function removeBlock() {
		if (delBlock && (await run.del(`${base}/schedule-blocks/${delBlock.id}`))) delOpen = false;
	}

	// --- lunch (its own runner, so its errors show in the Lunch card only)
	const lrun = new Runner();
	const lunch = $derived(data.lunch as LunchDoc | null);
	const SETTING_KEYS = ['duration_minutes', 'gap_before_minutes', 'gap_after_minutes', 'capacity_teams', 'interval_minutes'] as const;
	/** The form values of the lunch document. Built synchronously for the first (server) render: effects do not run there. */
	const lunchForm = (l: LunchDoc | null) => ({
		set: Object.fromEntries(SETTING_KEYS.map((k) => [k, String(l?.settings[k] ?? '')])) as Record<string, string>,
		days: Object.fromEntries((l?.days ?? []).map((d) => [d.date, { from: d.lunch_start ?? '', to: d.lunch_end ?? '' }])) as Record<string, { from: string; to: string }>
	});
	// svelte-ignore state_referenced_locally
	let lset = $state(lunchForm(data.lunch as LunchDoc | null).set);
	// svelte-ignore state_referenced_locally
	let ldays = $state(lunchForm(data.lunch as LunchDoc | null).days);
	let lresult = $state<LunchResult | null>(null);
	let lgenOpen = $state(false);
	let lclearOpen = $state(false);
	$effect(() => {
		const l = lunch;
		if (!l) return;
		const f = lunchForm(l);
		lset = f.set;
		ldays = f.days;
	});
	const lunchDirty = $derived(
		!!lunch && (SETTING_KEYS.some((k) => lset[k] !== String(lunch.settings[k] ?? '')) || lunch.days.some((d) => (ldays[d.date]?.from ?? '') !== (d.lunch_start ?? '') || (ldays[d.date]?.to ?? '') !== (d.lunch_end ?? '')))
	);
	const lerr = (k: string) => lrun.fields[`settings.${k}`] ?? lrun.fields[k];
	const lerrOther = $derived(Object.entries(lrun.fields).filter(([k]) => !SETTING_KEYS.some((s) => k === s || k === `settings.${s}`)).map(([, v]) => v));
	function saveLunch() {
		lresult = null;
		return lrun.put(`${base}/lunch`, { settings: Object.fromEntries(SETTING_KEYS.map((k) => [k, Number(lset[k])])), days: (lunch?.days ?? []).map((d) => ({ date: d.date, lunch_start: strOrNull(ldays[d.date]?.from), lunch_end: strOrNull(ldays[d.date]?.to) })) }, () => void (lrun.notice = t('tn.lunch.saved')), { notice: '' });
	}
	async function generateLunch() {
		lgenOpen = false;
		lresult = null;
		await lrun.post<LunchResult>(`${base}/lunch/generate`, duration ? { match_duration_minutes: numOrNull(duration) } : {}, (d) => void (lresult = d ?? null), { notice: '' });
	}
	async function clearLunch() {
		lclearOpen = false;
		lresult = null;
		await lrun.post(`${base}/lunch/clear`, {}, () => void (lrun.notice = t('tn.lunch.cleared')), { notice: '' });
	}

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.schedule') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.schedule'))}</title></svelte:head>

<div class="grid gap-4">
	{#if needsRegen}
		<Alert kind="warning" title={t('tn.sched.regen_title')}>{t('tn.sched.regen_body')}</Alert>
	{/if}
	<NoticeAlert message={run.notice} onhide={() => (run.notice = '')} />
	{#if run.error && !moveOpen && !blockOpen && !delOpen && !clearOpen}<Alert kind="error">{run.error}</Alert>{/if}

	{#if data.error || !sched}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		<div class="grid items-stretch gap-4 lg:grid-cols-2">
			<section class="card card-body card-hl h-full gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Wand2 size={16} class="shrink-0" aria-hidden="true" /> {t('tn.sched.generate_title')}</h2>
				{#if canOperate}
							<div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
								<div class="w-full max-w-sm flex-1">
									<Field label={t('tn.sched.duration')} type="number" min="1" bind:value={duration} placeholder={String(sched.tournament.default_match_duration_minutes ?? '')} help={t('tn.sched.duration_help')} />
								</div>
								<div class="flex flex-wrap items-center justify-end gap-2 sm:mt-[1.625rem]">
									<button class="btn btn-primary gap-2" type="button" disabled={run.busy} onclick={generate}><Wand2 size={16} /> {t('tn.sched.generate')}</button>
									<button class="btn btn-outline btn-error gap-2" type="button" disabled={run.busy} onclick={() => { run.clear(); clearOpen = true; }}><Eraser size={16} /> {t('tn.sched.clear')}</button>
								</div>
							</div>
			{/if}
			
			</section>
			<section class="card card-body card-hl h-full content-start gap-3">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><CalendarClock size={16} class="shrink-0" aria-hidden="true" /> {t('tn.sched.blocks')}</h2>
					{#if canOperate}<button class="btn btn-outline btn-sm gap-1" type="button" onclick={() => openBlock(null)}><Plus size={14} /> {t('tn.sched.add_block')}</button>{/if}
				</div>
				<p class="text-xs text-muted">{t('tn.sched.blocks_help')}</p>
				{#each sched.blocks as b (b.id)}
					<div class="flex flex-wrap items-center gap-3 rounded-lg border border-line p-3">
						<div class="min-w-0 flex-1">
							<strong class="text-ink">{b.title}</strong>{#if b.kind === 'lunch'} <span class="badge badge-sm badge-info">{t('tn.lunch.kind')}</span>{/if}
							<p class="font-mono text-xs text-muted">{fmtDateTime(b.starts_at)} – {fmtTime(b.ends_at)} · {b.field?.name ?? t('tn.sched.all_fields')}{b.teams.length ? ` · ${b.teams.map((x) => x.name).join(', ')}` : ''}</p>
						</div>
						{#if canOperate && b.kind !== 'lunch'}
							<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.sched.edit_block')} onclick={() => openBlock(b)}><Pencil size={13} /></button>
							<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.sched.delete_block')} onclick={() => { delBlock = b; run.clear(); delOpen = true; }}><Trash2 size={13} /></button>
						{/if}
					</div>
				{:else}
					<p class="text-sm text-muted">{t('tn.sched.no_blocks')}</p>
				{/each}
			</section>
		</div>

		<section class="card card-body gap-3" aria-labelledby="lunch-h">
			<h2 id="lunch-h" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><UtensilsCrossed size={16} class="shrink-0" aria-hidden="true" /> {t('tn.lunch.title')}</h2>
			{#if !lunch}
				<Alert kind="error">{t('tn.lunch.load_error')}{data.lunchError && data.lunchError !== 'error' ? ` (${data.lunchError})` : ''}</Alert>
			{:else if !lunch.enabled}
				<p class="text-sm text-muted">{t('tn.lunch.disabled')} <a class="link font-semibold" href="{TBASE}/{tn.id}">{t('tn.lunch.disabled_link')}</a>.</p>
			{:else}
				<p class="text-xs text-muted">{t('tn.lunch.help')}</p>
				<NoticeAlert message={lrun.notice} onhide={() => (lrun.notice = '')} />
				{#if lrun.error}<Alert kind="error">{lrun.error}{#each lerrOther as m (m)} {m}{/each}</Alert>{/if}
				<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
					<Field label={t('tn.lunch.duration')} type="number" min="1" bind:value={lset.duration_minutes} error={lerr('duration_minutes')} help={t('tn.lunch.duration_help')} disabled={!canOperate} />
					<Field label={t('tn.lunch.gap_before')} type="number" min="0" bind:value={lset.gap_before_minutes} error={lerr('gap_before_minutes')} help={t('tn.lunch.gap_before_help')} disabled={!canOperate} />
					<Field label={t('tn.lunch.gap_after')} type="number" min="0" bind:value={lset.gap_after_minutes} error={lerr('gap_after_minutes')} help={t('tn.lunch.gap_after_help')} disabled={!canOperate} />
					<Field label={t('tn.lunch.capacity')} type="number" min="1" bind:value={lset.capacity_teams} error={lerr('capacity_teams')} help={t('tn.lunch.capacity_help')} disabled={!canOperate} />
					<Field label={t('tn.lunch.interval')} type="number" min="0" bind:value={lset.interval_minutes} error={lerr('interval_minutes')} help={t('tn.lunch.interval_help')} disabled={!canOperate} />
				</div>
				<div class="overflow-x-auto rounded-lg border border-line">
					<table class="table">
						<thead>
							<tr>
								<th>{t('tn.lunch.col.day')}</th>
								<th class="hidden sm:table-cell">{t('tn.lunch.col.hours')}</th>
								<th>{t('tn.lunch.col.from')}</th>
								<th>{t('tn.lunch.col.to')}</th>
								<th class="hidden sm:table-cell">{t('tn.lunch.col.teams')}</th>
								<th>{t('tn.lunch.col.sittings')}</th>
							</tr>
						</thead>
						<tbody>
							{#each lunch.days as d (d.date)}
								<tr class="hover:bg-paper">
									<td class="font-semibold whitespace-nowrap text-ink">{fmtDay(d.date)}</td>
									<td class="hidden font-mono text-xs whitespace-nowrap sm:table-cell">{d.day_start}–{d.day_end}</td>
									<td>{#if ldays[d.date]}<input class="input input-bordered input-sm w-28" type="time" aria-label="{t('tn.lunch.col.from')} {fmtDay(d.date)}" bind:value={ldays[d.date].from} disabled={!canOperate} />{/if}</td>
									<td>{#if ldays[d.date]}<input class="input input-bordered input-sm w-28" type="time" aria-label="{t('tn.lunch.col.to')} {fmtDay(d.date)}" bind:value={ldays[d.date].to} disabled={!canOperate} />{/if}</td>
									<td class="hidden sm:table-cell">{d.teams_eating}</td>
									<td>
										<div class="flex flex-wrap gap-1.5">
											{#each d.sittings as s (s.start)}
												<span class="badge badge-ghost h-auto gap-1 py-1 font-normal whitespace-normal"><span class="font-mono text-xs">{s.start}-{s.end}</span> · {s.teams.map((x) => x.name).join(', ')}</span>
											{:else}
												<span class="text-sm text-muted">{t('tn.lunch.no_sittings')}</span>
											{/each}
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<p class="text-xs text-muted">{t('tn.lunch.days_help')}</p>
				{#if canOperate}
					<div class="flex flex-wrap items-center justify-end gap-3">
						{#if lunchDirty}<span class="mr-auto rounded-md border border-warn-border bg-warn-bg px-3 py-1.5 text-sm font-semibold text-warn-text">{t('tn.lunch.unsaved')}</span>{/if}
						<button class="btn btn-primary gap-2" type="button" disabled={lrun.busy || !lunchDirty} onclick={saveLunch}><Save size={16} /> {t('tn.lunch.save')}</button>
						<button class="btn btn-outline gap-2" type="button" disabled={lrun.busy || lunchDirty} onclick={() => { lrun.clear(); lgenOpen = true; }}><Wand2 size={16} /> {t('tn.lunch.generate')}</button>
						<button class="btn btn-outline btn-error gap-2" type="button" disabled={lrun.busy} onclick={() => { lrun.clear(); lclearOpen = true; }}><Eraser size={16} /> {t('tn.lunch.clear')}</button>
					</div>
				{/if}
				{#if lresult}
					<div class="grid gap-2 rounded-lg border border-line p-3">
						<div class="flex items-start justify-between gap-2">
							<p class="text-sm font-semibold text-ink">{t('tn.lunch.result', { sittings: lresult.sittings_created, games: lresult.games_replanned })}</p>
							<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.lunch.dismiss')} title={t('tn.lunch.dismiss')} onclick={() => (lresult = null)}><X size={13} /></button>
						</div>
						{#if lresult.note}<p class="text-sm text-muted">{lresult.note}</p>{/if}
						{#if lresult.problems.length}
							<h3 class="text-xs font-bold text-muted">{t('tn.lunch.problems')} ({lresult.problems.length})</h3>
							{#each lresult.problems as p, i (i)}
								<Alert kind="warning" title={t(`tn.lunch.problem.${p.type}`)}>{fmtDay(p.date)}{p.team ? ` · ${p.team.name}` : ''}: {p.message}</Alert>
							{/each}
						{:else}
							<p class="text-sm text-muted">{t('tn.lunch.no_problems')}</p>
						{/if}
					</div>
				{/if}
			{/if}
		</section>

		<section class="card card-body gap-4">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('tn.sched.games_title')}</h2>
				<div class="flex w-fit items-center gap-1" role="group" aria-label={t('tn.sched.view')}>
					{#each ['board', 'list'] as v (v)}
						<button type="button" aria-pressed={view === v} onclick={() => (view = v as 'board' | 'list')} class="rounded-md px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {view === v ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}">{t(`tn.sched.view_${v}`)}</button>
					{/each}
				</div>
		</div>

		{#if view === 'board'}
			{#if data.board}<ScheduleBoard board={data.board} {canOperate} busy={run.busy} onmove={drop} onedit={openMove} />{/if}
			{#if canOperate}<p class="text-xs text-muted">{t('tn.sched.drag_help')}</p>{/if}
		{:else}
			{#each sched.stages as st (st.stage.id)}
				<section class="grid gap-3 border-t border-line pt-4">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{st.stage.name}</h2>
					{#if st.rounds.length}
						<div class="overflow-x-auto rounded-lg border border-line">
							<table class="table table-sm w-full">
								<thead>
									<tr>
										<th class="w-8"></th>
										<th class="w-44">{t('tn.sched.col_start')}</th>
										<th class="w-48">{t('tn.sched.col_field')}</th>
										<th class="w-24">{t('tn.sched.col_group')}</th>
										<th>{t('tn.sched.col_match')}</th>
										<th class="w-28"></th>
									</tr>
								</thead>
								<tbody>
									{#each st.rounds as r (r.round_number)}
										{@const rows = sortedRound(r.matches)}
										<tr class="bg-subhead"><th colspan="6" class="text-left font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{r.label}</th></tr>
										{#each rows as m, i (m.id)}
											{@const movable = canOperate && !!m.scheduled_start && m.status !== 'completed'}
											{#if m.scheduled_start && (i === 0 || !rows[i - 1].scheduled_start || fmtDay(rows[i - 1].scheduled_start!) !== fmtDay(m.scheduled_start))}
												<tr><td colspan="6" class="bg-paper py-1 font-mono text-[11px] font-semibold text-muted">{fmtDay(m.scheduled_start)}</td></tr>
											{/if}
											<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
											<tr
												class="hover:bg-paper {dragMatch === m.id ? 'opacity-40' : ''} {overMatch === m.id && dragMatch !== m.id ? 'outline outline-2 -outline-offset-2 outline-primary' : ''}"
												draggable={movable}
												ondragstart={() => (dragMatch = m.id)}
												ondragover={(e) => {
													if (dragMatch && dragMatch !== m.id && movable && rows.some((x) => x.id === dragMatch)) {
														e.preventDefault();
														overMatch = m.id;
													}
												}}
												ondrop={(e) => {
													e.preventDefault();
													if (dragMatch) moveRow(rows, dragMatch, m.id);
												}}
												ondragend={() => (dragMatch = overMatch = null)}>
												<td class="text-muted">{#if movable}<GripVertical size={14} class="cursor-grab" aria-hidden="true" />{/if}</td>
												<td class="font-mono text-xs whitespace-nowrap">{m.scheduled_start ? fmtTime(m.scheduled_start) : t('tn.sched.unscheduled')}</td>
												<td class="text-xs">{m.field?.name ?? '—'}</td>
												<td>{#if m.group}<span class="badge badge-sm badge-ghost">{m.group.name}</span>{/if}</td>
												<td class="text-sm font-medium text-ink">{m.home_display} – {m.away_display}</td>
												<td>
													<div class="flex items-center justify-end gap-1">
														{#if movable}
															<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={run.busy || i === 0 || !rows[i - 1].scheduled_start} aria-label={t('tn.sched.move_up')} title={t('tn.sched.move_up')} onclick={() => stepRow(rows, i, -1)}><ArrowUp size={14} /></button>
															<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={run.busy || i === rows.length - 1 || !rows[i + 1].scheduled_start} aria-label={t('tn.sched.move_down')} title={t('tn.sched.move_down')} onclick={() => stepRow(rows, i, 1)}><ArrowDown size={14} /></button>
														{/if}
														{#if canOperate}<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.sched.reschedule')} title={t('tn.sched.reschedule')} onclick={() => openMove(m)}><Pencil size={13} /></button>{/if}
													</div>
												</td>
											</tr>
										{/each}
									{/each}
								</tbody>
							</table>
						</div>
						{#if canOperate}<p class="text-xs text-muted">{t('tn.sched.reorder_help')}</p>{/if}
					{:else}
						<p class="text-sm text-muted">{t('tn.sched.no_matches')}</p>
					{/if}
				</section>
			{:else}
				<p class="text-sm text-muted">{t('tn.sched.no_matches')}</p>
			{/each}
		{/if}
		</section>

	{/if}
</div>

<Modal bind:open={clearOpen} title={t('tn.sched.clear')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.sched.clear_body')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (clearOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error" type="button" disabled={run.busy} onclick={clear}>{t('tn.sched.clear')}</button>
		</div>
	</div>
</Modal>

<Modal bind:open={moveOpen} title={t('tn.sched.reschedule')}>
	<form class="grid gap-3" onsubmit={saveMove}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p class="text-sm font-semibold">{moving?.home_display} – {moving?.away_display}</p>
		<div class="form-control w-full">
			<label class="label" for="mv-field"><span class="label-text">{t('tn.sched.field')}</span></label>
			<select id="mv-field" class="select select-bordered w-full" bind:value={moveField} required>
				{#each sched?.fields ?? [] as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
			</select>
		</div>
		<Field label={t('tn.sched.start')} type="datetime-local" bind:value={moveStart} required />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (moveOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={blockOpen} title={editBlock ? t('tn.sched.edit_block') : t('tn.sched.add_block')}>
	<form class="grid gap-3" onsubmit={saveBlock}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<Field label={t('tn.sched.block_title')} bind:value={bf.title} error={run.fields.title} required />
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('tn.sched.block_from')} type="datetime-local" bind:value={bf.starts_at} error={run.fields.starts_at} required />
			<Field label={t('tn.sched.block_until')} type="datetime-local" bind:value={bf.ends_at} error={run.fields.ends_at} required />
		</div>
		<div class="form-control w-full">
			<label class="label" for="bl-field"><span class="label-text">{t('tn.sched.field')}</span></label>
			<select id="bl-field" class="select select-bordered w-full" bind:value={bf.field_id}>
				<option value="">{t('tn.sched.all_fields')}</option>
				{#each sched?.fields ?? [] as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
			</select>
		</div>
		<fieldset class="form-control">
			<legend class="label"><span class="label-text">{t('tn.sched.block_teams')}</span></legend>
			<div class="max-h-44 overflow-y-auto rounded-lg border border-line p-2">
				{#each data.teams as team (team.id)}
					<label class="label cursor-pointer justify-start gap-2 py-1"><input type="checkbox" class="checkbox checkbox-sm" checked={bf.team_ids.includes(team.id)} onchange={() => toggleTeam(team.id)} /><span class="label-text">{team.name}</span></label>
				{/each}
			</div>
			<span class="label-text-alt mt-1 block">{t('tn.sched.block_teams_help')}</span>
		</fieldset>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (blockOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delOpen} title={t('tn.sched.delete_block')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{delBlock?.title}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={removeBlock}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
<Modal bind:open={lgenOpen} title={t('tn.lunch.generate')}>
	<div class="grid gap-3">
		<p>{t('tn.lunch.generate_body')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (lgenOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="button" disabled={lrun.busy} onclick={generateLunch}>{t('tn.lunch.generate')}</button>
		</div>
	</div>
</Modal>

<Modal bind:open={lclearOpen} title={t('tn.lunch.clear')}>
	<div class="grid gap-3">
		<p>{t('tn.lunch.clear_body')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (lclearOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error" type="button" disabled={lrun.busy} onclick={clearLunch}>{t('tn.lunch.clear')}</button>
		</div>
	</div>
</Modal>
