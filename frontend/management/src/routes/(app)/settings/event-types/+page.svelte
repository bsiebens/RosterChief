<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, GripVertical, Pencil, Plus, Search, Shapes, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { renderTemplate } from '#lib/events/form-types.ts';
	import type { EventSubtype } from './+page.server.ts';

	let { data, form } = $props();

	const KINDS = ['training', 'game', 'tournament', 'meeting', 'social', 'other'];
	/** Kinds whose title is generated from the template when left empty; for the rest it is only a suggestion. */
	const GENERATED = ['training', 'game'];
	const kindLabel = (k: string) => t(`eventform.kind.${k}`);

	let kindFilter = $state('');
	let query = $state('');
	let formOpen = $state(false);
	let editing = $state<EventSubtype | null>(null);
	let kind = $state('training');
	let name = $state('');
	let template = $state('');
	let deleting = $state<EventSubtype | null>(null);
	let deleteOpen = $state(false);
	let busy = $state(false);

	// A game's {teams} is the match-up; elsewhere it is the team and group names.
	const example = (tpl: string, k: string) => renderTemplate(tpl, { teams: k === 'game' ? 'U12 vs Rivals' : 'U12', kind: kindLabel(k) });

	// Sections open by default; the order is changed within a kind (drag the rows or use the arrows), then saved.
	let closed = $state<Record<string, boolean>>({});
	let list = $state<EventSubtype[]>([]);
	$effect(() => {
		list = [...data.subtypes];
	});
	const shown = $derived(
		list.filter((s) => (!kindFilter || s.kind === kindFilter) && (!query.trim() || `${s.name} ${s.title_template}`.toLowerCase().includes(query.trim().toLowerCase())))
	);
	const sections = $derived(KINDS.map((k) => ({ kind: k, rows: shown.filter((s) => s.kind === k) })).filter((sec) => sec.rows.length));
	let reorderForm: HTMLFormElement | undefined = $state();
	let idsValue = $state('');
	let dragId = $state<string | null>(null);
	let overId = $state<string | null>(null);
	// Moving only makes sense while the whole kind is listed: a search narrows the rows, so ordering waits for it to be cleared.
	const canOrder = $derived(!query.trim());
	const persist = async () => {
		idsValue = list.map((s) => s.id).join(',');
		await Promise.resolve();
		reorderForm?.requestSubmit();
	};
	/** Move `id` to the place of `targetId` (same kind only), keeping the other kinds where they are. */
	const moveTo = (id: string, targetId: string) => {
		const from = list.findIndex((s) => s.id === id);
		const to = list.findIndex((s) => s.id === targetId);
		if (from < 0 || to < 0 || from === to || list[from].kind !== list[to].kind) return;
		const next = [...list];
		next.splice(to, 0, next.splice(from, 1)[0]);
		list = next;
		persist();
	};
	const step = (s: EventSubtype, dir: -1 | 1) => {
		const same = list.filter((x) => x.kind === s.kind);
		const target = same[same.indexOf(s) + dir];
		if (target) moveTo(s.id, target.id);
	};
	const onDrop = (targetId: string) => {
		if (dragId && dragId !== targetId) moveTo(dragId, targetId);
		dragId = overId = null;
	};

	function openForm(s: EventSubtype | null) {
		editing = s;
		kind = s?.kind ?? (kindFilter || 'training');
		name = s?.name ?? '';
		template = s?.title_template ?? '';
		formOpen = true;
	}
	function openDelete(s: EventSubtype) {
		deleting = s;
		deleteOpen = true;
	}
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
	const err = (k: string) => (form?.subAction === 'save' ? form.subFields?.[k] : undefined);

	usePageHeader(() => ({ title: t('eventtypes.title'), context: data.subtypes.length === 1 ? t('eventtypes.count_one') : t('eventtypes.count', { n: data.subtypes.length }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => openForm(null)}><Plus size={16} /> {t('eventtypes.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('eventtypes.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-7 py-2.5">
		<label class="input input-sm flex w-64 max-w-full items-center gap-2">
			<Search size={14} aria-hidden="true" />
			<input type="search" class="min-w-0 grow" placeholder={t('eventtypes.search')} aria-label={t('eventtypes.search')} bind:value={query} />
		</label>
		<select class="select select-sm w-44 max-w-full" aria-label={t('eventtypes.filter_kind')} bind:value={kindFilter}>
			<option value="">{t('eventtypes.all_kinds')}</option>
			{#each KINDS as k (k)}<option value={k}>{kindLabel(k)}</option>{/each}
		</select>
		<div class="flex-1"></div>
		<InfoTip label={t('eventtypes.info_label')} text={t('eventtypes.info')} align="right" />
	</div>

	<div class="grid gap-5 px-7 py-5">
		{#if form?.subAction === 'reorder'}<Alert kind="error" title={t('eventtypes.reorder_failed')}>{form.subError}</Alert>{/if}
		{#if data.error}
			<Alert kind="error">{t('eventtypes.load_failed')}</Alert>
		{:else if !data.subtypes.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Shapes size={28} />
				<p>{t('eventtypes.empty')}</p>
			</div>
		{:else if !sections.length}
			<div class="card card-body items-center py-10 text-center text-muted"><p>{t('eventtypes.no_match')}</p></div>
		{:else}
			<p class="text-sm text-muted">{canOrder ? t('eventtypes.drag_hint') : t('eventtypes.order_search_hint')}</p>
			<div class="card overflow-x-auto">
				<table class="table w-full table-fixed">
					<colgroup><col class="w-24" /><col class="w-[26%]" /><col /><col class="w-24" /><col class="w-56" /></colgroup>
					<thead>
						<tr><th class="px-4"><span class="sr-only">{t('eventtypes.col.move')}</span></th><th class="px-4">{t('eventtypes.col.name')}</th><th class="px-4">{t('eventtypes.col.template')}</th><th class="px-4 text-right">{t('eventtypes.col.in_use')}</th><th></th></tr>
					</thead>
					{#each sections as sec (sec.kind)}
						<tbody>
							<tr>
								<th colspan="5" class="bg-subhead p-0 text-left">
									<button type="button" class="flex w-full items-center gap-2 px-4 py-2 text-left hover:bg-paper" aria-expanded={!closed[sec.kind]} onclick={() => (closed[sec.kind] = !closed[sec.kind])}>
										{#if !closed[sec.kind]}<ChevronDown size={16} aria-hidden="true" />{:else}<ChevronRight size={16} aria-hidden="true" />{/if}
										<span class="font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{kindLabel(sec.kind)}</span>
										<span class="rounded-full border border-muted bg-white px-2 py-0.5 font-mono text-xs font-semibold text-slate">{sec.rows.length}</span>
										<span class="ml-2 text-xs font-normal tracking-normal text-muted normal-case">{GENERATED.includes(sec.kind) ? t('eventtypes.generated') : t('eventtypes.suggested')}</span>
									</button>
								</th>
							</tr>
							{#if !closed[sec.kind]}
								{#each sec.rows as s, i (s.id)}
									<tr
										class="hover:bg-surface {overId === s.id && dragId !== s.id ? 'outline outline-2 -outline-offset-2 outline-club' : ''} {dragId === s.id ? 'opacity-50' : ''}"
										draggable={canOrder}
										ondragstart={(e) => {
											dragId = s.id;
											e.dataTransfer?.setData('text/plain', s.id);
											if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
										}}
										ondragover={(e) => {
											if (!dragId) return;
											e.preventDefault();
											overId = s.id;
										}}
										ondrop={(e) => {
											e.preventDefault();
											onDrop(s.id);
										}}
										ondragend={() => (dragId = overId = null)}>
										<td class="px-4 whitespace-nowrap">
											{#if canOrder}
												<span class="inline-flex items-center gap-0.5 text-muted">
													<GripVertical size={16} class="cursor-grab" aria-hidden="true" />
													<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === 0} aria-label={t('eventtypes.move_up', { name: s.name })} onclick={() => step(s, -1)}><ArrowUp size={14} /></button>
													<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === sec.rows.length - 1} aria-label={t('eventtypes.move_down', { name: s.name })} onclick={() => step(s, 1)}><ArrowDown size={14} /></button>
												</span>
											{/if}
										</td>
										<td class="px-4"><button type="button" class="link text-left font-semibold" onclick={() => openForm(s)}>{s.name}</button></td>
										<td class="px-4">
											{#if s.title_template}
												<code class="text-xs">{s.title_template}</code>
												<div class="text-xs text-muted">{t('eventtypes.example', { title: example(s.title_template, s.kind) })}</div>
											{:else}<span class="text-muted">{t('eventtypes.template_default')}</span>{/if}
										</td>
										<td class="px-4 text-right font-mono text-xs">{s.in_use_count}</td>
										<td class="px-4 text-right whitespace-nowrap">
											<button class="btn btn-outline btn-xs" type="button" aria-label={t('eventtypes.edit_named', { name: s.name })} onclick={() => openForm(s)}><Pencil size={13} /> {t('common.edit')}</button>
											<button class="btn btn-outline btn-error btn-xs" type="button" aria-label={t('eventtypes.delete_named', { name: s.name })} onclick={() => openDelete(s)}><Trash2 size={13} /> {t('common.delete')}</button>
										</td>
									</tr>
								{/each}
							{/if}
						</tbody>
					{/each}
				</table>
			</div>
		{/if}
	</div>
</div>

<form method="POST" action="?/reorder" bind:this={reorderForm} use:enhance={() => async ({ update }) => await update({ reset: false })}>
	<input type="hidden" name="ids" value={idsValue} />
</form>

<Modal bind:open={formOpen} title={editing ? t('eventtypes.edit') : t('eventtypes.add')}>
	<form method="POST" action="?/save" class="grid gap-3" use:enhance={submit(() => (formOpen = false))}>
		<input type="hidden" name="id" value={editing?.id ?? ''} />
		{#if form?.subAction === 'save' && form.subError && !Object.keys(form.subFields ?? {}).length}<Alert kind="error" title={t('eventtypes.save_failed')}>{form.subError}</Alert>{/if}
		<div class="form-control w-full">
			<label class="label" for="subtype-kind"><span class="label-text">{t('eventtypes.kind')}</span></label>
			<select id="subtype-kind" name="kind" class="select w-full" bind:value={kind} disabled={!!editing && editing.in_use_count > 0}>
				{#each KINDS as k (k)}<option value={k}>{kindLabel(k)}</option>{/each}
			</select>
			{#if editing && editing.in_use_count > 0}<input type="hidden" name="kind" value={kind} /><span class="mt-1 block text-xs text-muted">{t('eventtypes.kind_locked')}</span>{/if}
			{#if err('kind')}<span class="mt-1 block text-xs text-error">{err('kind')}</span>{/if}
		</div>
		<Field label={t('eventtypes.name')} name="name" required bind:value={name} error={err('name')} autocomplete="off" maxlength={100} />
		<Field label={t('eventtypes.template')} name="title_template" bind:value={template} error={err('title_template')} autocomplete="off" maxlength={255} placeholder={GENERATED.includes(kind) ? '{kind} {teams}' : ''} help={GENERATED.includes(kind) ? t('eventtypes.template_help_generated') : t('eventtypes.template_help_suggested')} />
		<p class="text-xs text-muted">{t('eventtypes.placeholders')}{#if template.trim()} · {t('eventtypes.example', { title: example(template, kind) })}{/if}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (formOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('eventtypes.delete_title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={submit(() => (deleteOpen = false))}>
		<input type="hidden" name="id" value={deleting?.id ?? ''} />
		{#if form?.subAction === 'delete' && form.subError}<Alert kind="error" title={t('eventtypes.delete_failed')}>{form.subError}</Alert>{/if}
		<p>{t('eventtypes.delete_body', { name: deleting?.name ?? '' })}</p>
		{#if deleting && deleting.in_use_count > 0}<p class="text-sm text-muted">{t('eventtypes.delete_in_use', { n: deleting.in_use_count })}</p>{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
