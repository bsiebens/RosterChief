<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { ChevronRight, Hash, Pencil, Plus, Trash2, Bookmark } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Pool, Reservation } from './+page.server.ts';

	let { data, form } = $props();

	let formOpen = $state(false);
	let editing = $state<Pool | null>(null);
	let name = $state('');
	let min = $state('1');
	let max = $state('99');
	let gap = $state('0');
	let deleting = $state<Pool | null>(null);
	let deleteOpen = $state(false);
	let resOpen = $state(false);
	let resPool = $state<Pool | null>(null);
	let reservations = $state<Reservation[]>([]);
	let resLoadFailed = $state(false);
	let resNumber = $state('');
	let resNote = $state('');
	let busy = $state(false);

	function openForm(p: Pool | null) {
		editing = p;
		name = p?.name ?? '';
		min = String(p?.min_number ?? 1);
		max = String(p?.max_number ?? 99);
		gap = String(p?.min_age_gap_years ?? 5);
		formOpen = true;
	}
	function openDelete(p: Pool) {
		deleting = p;
		deleteOpen = true;
	}
	async function loadReservations(id: string) {
		try {
			const res = await fetch(`/api/v1/number-pools/${id}/reservations`, { headers: { accept: 'application/json' } });
			if (!res.ok) throw new Error(String(res.status));
			reservations = await res.json();
			resLoadFailed = false;
		} catch {
			resLoadFailed = true;
		}
	}
	function openReservations(p: Pool) {
		resPool = p;
		reservations = [];
		resNumber = '';
		resNote = '';
		resOpen = true;
		loadReservations(p.id);
	}
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
	// Reservation changes keep the modal open and refresh its list (the page reload also updates the counts).
	const submitRes = () => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success' && resPool) {
				resNumber = '';
				resNote = '';
				await loadReservations(resPool.id);
			}
		};
	};
	const err = (k: string) => (form?.poolAction === 'save' ? form.poolFields?.[k] : undefined);
	const resErr = (k: string) => (form?.poolAction === 'reserve' ? form.poolFields?.[k] : undefined);

	usePageHeader(() => ({ title: t('pools.title'), context: data.pools.length === 1 ? t('pools.count_one') : t('pools.count', { n: data.pools.length }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => openForm(null)}><Plus size={16} /> {t('pools.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('pools.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-6 py-2.5">
		<div class="flex-1"></div>
		<InfoTip label={t('pools.info_label')} text={t('pools.info')} align="right" />
	</div>

	<div class="grid gap-4 px-6 py-4">
		{#if data.error}
			<Alert kind="error">{t('pools.load_failed')}</Alert>
		{:else if !data.pools.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Hash size={28} />
				<p>{t('pools.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>{t('pools.col.name')}</th><th>{t('pools.col.range')}</th><th class="hidden lg:table-cell">{t('pools.col.gap')}</th><th class="hidden md:table-cell">{t('pools.col.teams')}</th><th class="text-right">{t('pools.col.reservations')}</th><th></th></tr>
					</thead>
					<tbody>
						{#each data.pools as p (p.id)}
							<tr class="hover:bg-paper">
								<td><button type="button" class="link text-left font-semibold" onclick={() => openForm(p)}>{p.name}</button></td>
								<td class="font-mono text-xs">{p.min_number}–{p.max_number}</td>
								<td class="hidden font-mono text-xs lg:table-cell">{t('pools.years', { n: p.min_age_gap_years })}</td>
								<td class="hidden md:table-cell">
									<div class="flex flex-wrap gap-1">
										{#each p.teams as team (team.id)}<span class="badge badge-sm badge-outline">{team.name}</span>{:else}<span class="text-sm text-muted">—</span>{/each}
									</div>
								</td>
								<td class="text-right font-mono text-xs">{p.reservation_count}</td>
								<td class="text-right whitespace-nowrap">
									<button class="btn btn-outline btn-xs" type="button" onclick={() => openReservations(p)}><Bookmark size={13} /> {t('pools.reservations')}</button>
									<button class="btn btn-ghost btn-xs btn-square text-muted" type="button" aria-label={t('pools.edit_named', { name: p.name })} onclick={() => openForm(p)}><ChevronRight size={16} /></button>
									<button class="btn btn-ghost btn-xs btn-square text-error" type="button" aria-label={t('pools.delete_named', { name: p.name })} onclick={() => openDelete(p)}><Trash2 size={14} /></button>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open={formOpen} title={editing ? t('pools.edit') : t('pools.add')}>
	<form method="POST" action="?/save" class="grid gap-3" use:enhance={submit(() => (formOpen = false))}>
		<input type="hidden" name="id" value={editing?.id ?? ''} />
		{#if form?.poolAction === 'save' && form.poolError && !Object.keys(form.poolFields ?? {}).length}<Alert kind="error" title={t('pools.save_failed')}>{form.poolError}</Alert>{/if}
		<Field label={t('pools.name')} name="name" required bind:value={name} error={err('name')} autocomplete="off" />
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('pools.min')} name="min_number" type="number" min="0" required bind:value={min} error={err('min_number')} />
			<Field label={t('pools.max')} name="max_number" type="number" min="0" required bind:value={max} error={err('max_number')} />
		</div>
		<Field label={t('pools.gap')} name="min_age_gap_years" type="number" min="0" bind:value={gap} error={err('min_age_gap_years')} help={t('pools.gap_help')} />
		<fieldset class="form-control w-full">
			<legend class="label"><span class="label-text">{t('pools.teams')}</span></legend>
			{#if data.teams.length}
				<div class="max-h-56 overflow-y-auto rounded-lg border border-line p-2">
					{#each data.teams as team (team.id)}
						<label class="label cursor-pointer justify-start gap-2 py-1">
							<input type="checkbox" name="team_ids" value={team.id} class="checkbox checkbox-sm" checked={editing?.team_ids.includes(team.id) ?? false} />
							<span class="label-text">{team.name}</span>
						</label>
					{/each}
				</div>
			{:else}
				<p class="text-sm text-muted">{t('pools.teams_empty')}</p>
			{/if}
			{#if err('team_ids')}<span class="mt-1 block text-xs text-error">{err('team_ids')}</span>{/if}
		</fieldset>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (formOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('pools.delete_title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={submit(() => (deleteOpen = false))}>
		<input type="hidden" name="id" value={deleting?.id ?? ''} />
		{#if form?.poolAction === 'delete' && form.poolError}<Alert kind="error" title={t('pools.delete_failed')}>{form.poolError}</Alert>{/if}
		<p>{t('pools.delete_body', { name: deleting?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={resOpen} title={t('pools.reservations_title', { name: resPool?.name ?? '' })}>
	<div class="grid gap-3">
		{#if resLoadFailed}<Alert kind="error">{t('pools.res.load_failed')}</Alert>{/if}
		{#if form?.poolAction === 'release' && form.poolError}<Alert kind="error" title={t('pools.res.failed')}>{form.poolError}</Alert>{/if}
		{#if reservations.length}
			<ul class="divide-y divide-line rounded-lg border border-line">
				{#each reservations as r (r.id)}
					<li class="flex items-center gap-3 px-3 py-2">
						<span class="badge badge-outline font-mono text-xs">{r.number}</span>
						<span class="min-w-0 flex-1 text-sm">
							<span class="block truncate">{r.note || '—'}</span>
							<span class="block truncate text-xs text-muted">{r.reserved_by_name ? t('pools.res.by', { name: r.reserved_by_name }) : t('pools.res.by_unknown')}</span>
						</span>
						{#if r.can_release !== false}
							<form method="POST" action="?/release" use:enhance={submitRes()}>
								<input type="hidden" name="pool" value={resPool?.id ?? ''} />
								<input type="hidden" name="rid" value={r.id} />
								<button class="btn btn-outline btn-error btn-xs" type="submit" disabled={busy} aria-label={t('pools.res.release_named', { n: r.number })}><Trash2 size={13} /> {t('pools.res.release')}</button>
							</form>
						{:else}
							<span class="text-xs text-muted">{t('pools.res.locked')}</span>
						{/if}
					</li>
				{/each}
			</ul>
		{:else if !resLoadFailed}
			<p class="text-sm text-muted">{t('pools.res.empty')}</p>
		{/if}
		<form method="POST" action="?/reserve" class="grid gap-3 border-t border-line pt-3" use:enhance={submitRes()}>
			<input type="hidden" name="pool" value={resPool?.id ?? ''} />
			{#if form?.poolAction === 'reserve' && form.poolError && !Object.keys(form.poolFields ?? {}).length}<Alert kind="error" title={t('pools.res.failed')}>{form.poolError}</Alert>{/if}
			<div class="grid grid-cols-[7rem_1fr] gap-3">
				<Field label={t('pools.res.number')} name="number" type="number" min="0" required bind:value={resNumber} error={resErr('number')} />
				<Field label={t('pools.res.note')} name="note" bind:value={resNote} error={resErr('note')} placeholder={t('pools.res.note_placeholder')} autocomplete="off" />
			</div>
			<div class="modal-action mt-0 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (resOpen = false)}>{t('pools.close')}</button>
				<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Plus size={16} /> {t('pools.res.add')}</button>
			</div>
		</form>
	</div>
</Modal>
