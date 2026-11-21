<script lang="ts">
	import { BedDouble, Plus, TriangleAlert, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, fromLocalInput, Runner, strOrNull, TBASE, type TTeamRef } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const canOperate = $derived(tn.can_operate);
	const base = $derived(`/tournaments/${tn.id}/dressing-rooms`);
	const run = new Runner();

	let open = $state(false);
	let team = $state<TTeamRef | null>(null);
	let f = $state({ room: '', from: '', until: '', note: '' });
	function openAssign(t_: TTeamRef) {
		team = t_;
		f = { room: data.data?.rooms[0]?.id ?? '', from: '', until: '', note: '' };
		run.clear();
		open = true;
	}
	async function assign(e: SubmitEvent) {
		e.preventDefault();
		if (!team) return;
		const body = { dressing_room_id: f.room, tournament_team_id: team.id, assigned_from: strOrNull(fromLocalInput(f.from)), assigned_until: strOrNull(fromLocalInput(f.until)), note: f.note.trim() };
		if (await run.post(`${base}/assignments`, body)) open = false;
	}
	const release = (id: string) => run.del(`${base}/assignments/${id}`);

	usePageHeader(() => ({ title: tn.name, context: t('tn.tab.rooms') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.rooms'))}</title></svelte:head>

<div class="grid gap-3">
	{#if run.error && !open}<Alert kind="error">{run.error}</Alert>{/if}
	{#if data.error || !data.data}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#if !data.data.rooms.length}
			<Alert kind="info">{t('tn.rooms.none_defined')} {#if tn.can_manage}<a class="link" href="{TBASE}/dressing-rooms">{t('tn.rooms.manage_link')}</a>{/if}</Alert>
		{/if}
		{#each data.data.teams as row (row.team.id)}
			<section class="card card-body gap-2">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><BedDouble size={16} class="shrink-0" aria-hidden="true" /> {row.team.name}</h2>
					{#if canOperate && data.data.rooms.length}<button class="btn btn-outline btn-sm gap-1" type="button" onclick={() => openAssign(row.team)}><Plus size={14} /> {t('tn.rooms.assign')}</button>{/if}
				</div>
				{#each row.assignments as a (a.id)}
					<div class="flex flex-wrap items-center gap-3 rounded-lg border border-line p-2.5">
						<div class="min-w-0 flex-1">
							<strong class="text-ink">{a.room.name}</strong>
							<span class="font-mono text-xs text-muted"> · {a.assigned_from || a.assigned_until ? `${fmtDateTime(a.assigned_from)} – ${fmtDateTime(a.assigned_until)}` : t('tn.rooms.whole_tournament')}</span>
							{#if a.note}<p class="text-xs text-muted">{a.note}</p>{/if}
							{#each a.conflicts as c (c.assignment_id)}
								<p class="flex items-center gap-1 text-xs text-warning"><TriangleAlert size={12} /> {t('tn.rooms.conflict', { label: c.label })}</p>
							{/each}
						</div>
						{#if canOperate}<button class="btn btn-ghost btn-xs gap-1 text-error" type="button" disabled={run.busy} onclick={() => release(a.id)}><X size={13} /> {t('tn.rooms.release')}</button>{/if}
					</div>
				{:else}
					<p class="text-sm text-muted">{t('tn.rooms.no_assignments')}</p>
				{/each}
			</section>
		{:else}
			<div class="card card-body items-center py-10 text-center text-muted"><p>{t('tn.teams.empty')}</p></div>
		{/each}
	{/if}
</div>

<Modal bind:open title={t('tn.rooms.assign_title', { name: team?.name ?? '' })}>
	<form class="grid gap-3" onsubmit={assign}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<div class="form-control w-full">
			<label class="label" for="as-room"><span class="label-text">{t('tn.rooms.room')}</span></label>
			<select id="as-room" class="select select-bordered w-full" bind:value={f.room} required>
				{#each data.data?.rooms ?? [] as r (r.id)}<option value={r.id}>{r.name}{r.capacity ? ` (${r.capacity})` : ''}</option>{/each}
			</select>
		</div>
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('tn.rooms.from')} type="datetime-local" bind:value={f.from} error={run.fields.assigned_from} />
			<Field label={t('tn.rooms.until')} type="datetime-local" bind:value={f.until} error={run.fields.assigned_until} />
		</div>
		<p class="label-text-alt">{t('tn.rooms.window_help')}</p>
		<Field label={t('tn.rooms.note')} bind:value={f.note} error={run.fields.note} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('tn.rooms.assign')}</button>
		</div>
	</form>
</Modal>
