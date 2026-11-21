<script lang="ts">
	import { BedDouble, Pencil, Plus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { numOrNull, Runner } from '#lib/tournaments.svelte.ts';
	import type { Room } from './+page.server.ts';

	let { data } = $props();
	const run = new Runner();
	let open = $state(false);
	let editing = $state<Room | null>(null);
	let f = $state({ name: '', capacity: '', notes: '' });
	let del = $state<Room | null>(null);
	let delOpen = $state(false);

	function openForm(r: Room | null) {
		editing = r;
		f = { name: r?.name ?? '', capacity: r?.capacity === null || r?.capacity === undefined ? '' : String(r.capacity), notes: r?.notes ?? '' };
		run.clear();
		open = true;
	}
	async function save(e: SubmitEvent) {
		e.preventDefault();
		const body = { name: f.name.trim(), capacity: numOrNull(f.capacity), notes: f.notes };
		if (await (editing ? run.patch(`/dressing-rooms/${editing.id}`, body) : run.post('/dressing-rooms', body))) open = false;
	}
	async function remove() {
		if (del && (await run.del(`/dressing-rooms/${del.id}`))) delOpen = false;
	}

	usePageHeader(() => ({ title: t('tn.rooms.title'), context: t('tn.rooms.count', { n: data.rooms.length }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => openForm(null)}><Plus size={16} /> {t('tn.rooms.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('tn.rooms.title'))}</title></svelte:head>

<div class="grid gap-4">
	<p class="text-sm text-muted">{t('tn.rooms.help')}</p>
	{#if data.error}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if !data.rooms.length}
		<div class="card card-body items-center py-10 text-center text-muted"><BedDouble size={28} /><p>{t('tn.rooms.empty')}</p></div>
	{:else}
		<div class="card overflow-x-auto">
			<table class="table">
				<thead><tr><th>{t('tn.rooms.name')}</th><th class="text-right">{t('tn.rooms.capacity')}</th><th class="hidden md:table-cell">{t('tn.rooms.notes')}</th><th></th></tr></thead>
				<tbody>
					{#each data.rooms as r (r.id)}
						<tr class="hover:bg-paper">
							<td><button type="button" class="link text-left font-semibold" onclick={() => openForm(r)}>{r.name}</button></td>
							<td class="text-right font-mono text-xs">{r.capacity ?? '—'}</td>
							<td class="hidden text-sm text-muted md:table-cell">{r.notes || '—'}</td>
							<td class="text-right whitespace-nowrap"><div class="flex items-center justify-end gap-1">
								<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.rooms.edit_named', { name: r.name })} onclick={() => openForm(r)}><Pencil size={13} /></button>
								<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.rooms.delete_named', { name: r.name })} onclick={() => { del = r; run.clear(); delOpen = true; }}><Trash2 size={13} /></button>
							</div></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<Modal bind:open title={editing ? t('tn.rooms.edit') : t('tn.rooms.add')}>
	<form class="grid gap-3" onsubmit={save}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<Field label={t('tn.rooms.name')} bind:value={f.name} error={run.fields.name} required />
		<Field label={t('tn.rooms.capacity')} type="number" min="0" bind:value={f.capacity} error={run.fields.capacity} />
		<div class="form-control w-full">
			<label class="label" for="room-notes"><span class="label-text">{t('tn.rooms.notes')}</span></label>
			<textarea id="room-notes" class="textarea textarea-bordered min-h-20 w-full" bind:value={f.notes}></textarea>
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delOpen} title={t('tn.rooms.delete_title')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.rooms.delete_body', { name: del?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={remove}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
