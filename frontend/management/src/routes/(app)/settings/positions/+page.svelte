<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { ChevronRight, ArrowDown, ArrowUp, GripVertical, Plus, Trash2, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Position } from './+page.server.ts';

	let { data, form } = $props();

	let formOpen = $state(false);
	let editing = $state<Position | null>(null);
	let name = $state('');
	let shortName = $state('');
	let staff = $state(false);
	let management = $state(false);
	let deleting = $state<Position | null>(null);
	let deleteOpen = $state(false);
	let busy = $state(false);

	// The visible order: follows the server, changes at once while dragging or using the arrows, then is saved.
	let list = $state<Position[]>([]);
	$effect(() => {
		list = [...(data.positions ?? [])];
	});
	const nextOrdering = $derived(Math.max(0, ...(data.positions ?? []).map((p) => p.ordering)) + 1);
	let reorderForm: HTMLFormElement | undefined = $state();
	let idsValue = $state('');
	let dragId = $state<string | null>(null);
	let overId = $state<string | null>(null);
	const persist = async () => {
		idsValue = list.map((p) => p.id).join(',');
		await Promise.resolve();
		reorderForm?.requestSubmit();
	};
	const move = (from: number, to: number) => {
		if (from === to || to < 0 || to >= list.length) return;
		const next = [...list];
		next.splice(to, 0, next.splice(from, 1)[0]);
		list = next;
		persist();
	};
	const onDrop = (targetId: string) => {
		if (dragId && dragId !== targetId) move(list.findIndex((p) => p.id === dragId), list.findIndex((p) => p.id === targetId));
		dragId = overId = null;
	};

	function openForm(p: Position | null) {
		editing = p;
		name = p?.name ?? '';
		shortName = p?.short_name ?? '';
		staff = p?.staff_position ?? false;
		management = p?.management_position ?? false;
		formOpen = true;
	}
	function openDelete(p: Position) {
		deleting = p;
		deleteOpen = true;
	}
	// Management implies staff: ticking management ticks staff; clearing staff clears management.
	const onStaff = () => {
		if (!staff) management = false;
	};
	const onManagement = () => {
		if (management) staff = true;
	};
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
	const err = (k: string) => (form?.posAction === 'save' ? form.posFields?.[k] : undefined);

	usePageHeader(() => ({ title: t('positions.title'), context: data.positions.length === 1 ? t('positions.count_one') : t('positions.count', { n: data.positions.length }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => openForm(null)}><Plus size={16} /> {t('positions.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('positions.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-6 py-2.5">
		<div class="flex-1"></div>
		<InfoTip label={t('positions.info_label')} text={t('positions.info')} align="right" />
	</div>

	<div class="grid gap-4 px-6 py-4">
		{#if form?.posAction === 'reorder'}<Alert kind="error" title={t('positions.reorder_failed')}>{form.posError}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('positions.load_failed')}</Alert>
		{:else if !data.positions.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Users size={28} />
				<p>{t('positions.empty')}</p>
			</div>
		{:else}
			<p class="text-sm text-muted">{t('positions.drag_hint')}</p>
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th class="w-16"><span class="sr-only">{t('positions.col.move')}</span></th><th>{t('positions.col.name')}</th><th class="hidden md:table-cell">{t('positions.col.short')}</th><th>{t('positions.col.type')}</th><th class="text-right">{t('positions.col.in_use')}</th><th></th></tr>
					</thead>
					<tbody>
						{#each list as p, i (p.id)}
							<tr
								class="hover:bg-paper {overId === p.id && dragId !== p.id ? 'outline outline-2 -outline-offset-2 outline-club' : ''} {dragId === p.id ? 'opacity-50' : ''}"
								draggable="true"
								ondragstart={(e) => {
									dragId = p.id;
									e.dataTransfer?.setData('text/plain', p.id);
									if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
								}}
								ondragover={(e) => {
									e.preventDefault();
									overId = p.id;
								}}
								ondrop={(e) => {
									e.preventDefault();
									onDrop(p.id);
								}}
								ondragend={() => (dragId = overId = null)}>
								<td class="whitespace-nowrap">
									<span class="inline-flex items-center gap-0.5 text-muted">
										<GripVertical size={16} class="cursor-grab" aria-hidden="true" />
										<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === 0} aria-label={t('positions.move_up', { name: p.name })} onclick={() => move(i, i - 1)}><ArrowUp size={14} /></button>
										<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === list.length - 1} aria-label={t('positions.move_down', { name: p.name })} onclick={() => move(i, i + 1)}><ArrowDown size={14} /></button>
									</span>
								</td>
								<td><button type="button" class="link text-left font-semibold" onclick={() => openForm(p)}>{p.name}</button></td>
								<td class="hidden md:table-cell">{p.short_name}</td>
								<td>
									<div class="flex flex-wrap gap-1">
										{#if p.management_position}<span class="badge badge-sm badge-primary">{t('positions.type.management')}</span>{/if}
										{#if p.staff_position}<span class="badge badge-sm badge-ghost">{t('positions.type.staff')}</span>{:else}<span class="badge badge-sm badge-outline">{t('positions.type.player')}</span>{/if}
									</div>
								</td>
								<td class="text-right font-mono text-xs">{p.in_use_count}</td>
								<td class="text-right whitespace-nowrap"><div class="flex items-center justify-end gap-1">
									<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('positions.delete_named', { name: p.name })} onclick={() => openDelete(p)}><Trash2 size={13} /></button>
									<button class="btn btn-ghost btn-xs btn-square text-muted" type="button" aria-label={t('positions.edit_named', { name: p.name })} onclick={() => openForm(p)}><ChevronRight size={16} /></button>
								</div></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<form
	method="POST"
	action="?/reorder"
	bind:this={reorderForm}
	use:enhance={() => async ({ update }) => {
		await update({ reset: false });
	}}>
	<input type="hidden" name="ids" value={idsValue} />
</form>

<Modal bind:open={formOpen} title={editing ? t('positions.edit') : t('positions.add')}>
	<form method="POST" action="?/save" class="grid gap-3" use:enhance={submit(() => (formOpen = false))}>
		<input type="hidden" name="id" value={editing?.id ?? ''} />
		{#if form?.posAction === 'save' && form.posError && !Object.keys(form.posFields ?? {}).length}<Alert kind="error" title={t('positions.save_failed')}>{form.posError}</Alert>{/if}
		<Field label={t('positions.name')} name="name" required bind:value={name} error={err('name')} autocomplete="off" />
		<Field label={t('positions.short_name')} name="short_name" bind:value={shortName} error={err('short_name')} autocomplete="off" />
		{#if !editing}<input type="hidden" name="ordering" value={nextOrdering} />{/if}
		<label class="label cursor-pointer justify-start gap-2 py-1">
			<input type="checkbox" name="staff_position" class="toggle" bind:checked={staff} onchange={onStaff} />
			<span class="label-text">{t('positions.staff')}</span>
		</label>
		<label class="label cursor-pointer justify-start gap-2 py-1">
			<input type="checkbox" name="management_position" class="toggle" bind:checked={management} onchange={onManagement} />
			<span class="label-text">{t('positions.management')}</span>
		</label>
		{#if err('management_position')}<span class="block text-xs text-error">{err('management_position')}</span>{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (formOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('positions.delete_title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={submit(() => (deleteOpen = false))}>
		<input type="hidden" name="id" value={deleting?.id ?? ''} />
		{#if form?.posAction === 'delete' && form.posError}<Alert kind="error" title={t('positions.delete_failed')}>{form.posError}</Alert>{/if}
		<p>{t('positions.delete_body', { name: deleting?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
