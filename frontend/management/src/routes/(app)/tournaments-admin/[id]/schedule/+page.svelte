<script lang="ts">
	import { CalendarClock, Eraser, Pencil, Plus, Trash2, Wand2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import ScheduleBoard from '#lib/components/tournaments/ScheduleBoard.svelte';
	import Toast from '#lib/components/Toast.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, fmtTime, fromLocalInput, numOrNull, Runner, strOrNull, toLocalInput, type MatchDoc, type ScheduleBlock } from '#lib/tournaments.svelte.ts';

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

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.schedule') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.schedule'))}</title></svelte:head>

<div class="grid gap-4">
	{#if needsRegen}
		<Alert kind="warning" title={t('tn.sched.regen_title')}>{t('tn.sched.regen_body')}</Alert>
	{/if}
	{#if run.error && !moveOpen && !blockOpen && !delOpen && !clearOpen}<Alert kind="error">{run.error}</Alert>{/if}

	{#if data.error || !sched}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#if canOperate}
			<section class="card card-body card-hl gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Wand2 size={16} class="shrink-0" aria-hidden="true" /> {t('tn.sched.generate_title')}</h2>
				<div class="flex flex-wrap items-end gap-3">
					<div class="w-48">
						<Field label={t('tn.sched.duration')} type="number" min="1" bind:value={duration} placeholder={String(sched.tournament.default_match_duration_minutes ?? '')} help={t('tn.sched.duration_help')} />
					</div>
					<button class="btn btn-primary gap-2" type="button" disabled={run.busy} onclick={generate}><Wand2 size={16} /> {t('tn.sched.generate')}</button>
					<button class="btn btn-outline btn-error gap-2" type="button" disabled={run.busy} onclick={() => { run.clear(); clearOpen = true; }}><Eraser size={16} /> {t('tn.sched.clear')}</button>
				</div>
			</section>
		{/if}

		<div class="flex w-fit items-center gap-1" role="group" aria-label={t('tn.sched.view')}>
			{#each ['board', 'list'] as v (v)}
				<button type="button" aria-pressed={view === v} onclick={() => (view = v as 'board' | 'list')} class="rounded-md px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {view === v ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}">{t(`tn.sched.view_${v}`)}</button>
			{/each}
		</div>

		{#if view === 'board'}
			{#if data.board}<ScheduleBoard board={data.board} {canOperate} busy={run.busy} onmove={drop} onedit={openMove} />{/if}
			{#if canOperate}<p class="text-xs text-muted">{t('tn.sched.drag_help')}</p>{/if}
		{:else}
			{#each sched.stages as st (st.stage.id)}
				<section class="card card-body gap-3">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{st.stage.name}</h2>
					{#each st.rounds as r (r.round_number)}
						<div class="grid gap-1">
							<h3 class="text-xs font-bold text-muted">{r.label}</h3>
							<div class="overflow-x-auto">
								<table class="table table-sm">
									<tbody>
										{#each r.matches as m (m.id)}
											<tr class="hover:bg-paper">
												<td class="w-40 font-mono text-xs whitespace-nowrap">{m.scheduled_start ? fmtDateTime(m.scheduled_start) : t('tn.sched.unscheduled')}</td>
												<td class="w-32 text-xs">{m.field?.name ?? '—'}</td>
												<td class="text-sm">{m.home_display} – {m.away_display}{#if m.group} <span class="badge badge-ghost badge-xs">{m.group.name}</span>{/if}</td>
												<td class="w-10 text-right"><div class="flex items-center justify-end gap-1">{#if canOperate}<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.sched.reschedule')} onclick={() => openMove(m)}><Pencil size={13} /></button>{/if}</div></td>
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
				<p class="text-sm text-muted">{t('tn.sched.no_matches')}</p>
			{/each}
		{/if}

		<section class="card card-body card-hl gap-3">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><CalendarClock size={16} class="shrink-0" aria-hidden="true" /> {t('tn.sched.blocks')}</h2>
				{#if canOperate}<button class="btn btn-outline btn-sm gap-1" type="button" onclick={() => openBlock(null)}><Plus size={14} /> {t('tn.sched.add_block')}</button>{/if}
			</div>
			<p class="text-xs text-muted">{t('tn.sched.blocks_help')}</p>
			{#each sched.blocks as b (b.id)}
				<div class="flex flex-wrap items-center gap-3 rounded-lg border border-line p-3">
					<div class="min-w-0 flex-1">
						<strong class="text-ink">{b.title}</strong>
						<p class="font-mono text-xs text-muted">{fmtDateTime(b.starts_at)} – {fmtTime(b.ends_at)} · {b.field?.name ?? t('tn.sched.all_fields')}{b.teams.length ? ` · ${b.teams.map((x) => x.name).join(', ')}` : ''}</p>
					</div>
					{#if canOperate}
						<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.sched.edit_block')} onclick={() => openBlock(b)}><Pencil size={13} /></button>
						<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.sched.delete_block')} onclick={() => { delBlock = b; run.clear(); delOpen = true; }}><Trash2 size={13} /></button>
					{/if}
				</div>
			{:else}
				<p class="text-sm text-muted">{t('tn.sched.no_blocks')}</p>
			{/each}
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
<Toast message={run.notice} onhide={() => (run.notice = '')} />
