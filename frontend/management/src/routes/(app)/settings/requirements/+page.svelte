<script lang="ts">
	import { enhance } from '$app/forms';
	import { ChevronRight, FileCheck, Pencil, Plus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { EVENT_KINDS, hasChoices, optionLines, QUESTION_TYPES, type RequirementKind, type RequirementListItem } from '#lib/requirements.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();

	let formOpen = $state(false);
	let editing = $state<RequirementListItem | null>(null);
	let name = $state('');
	let description = $state('');
	let kind = $state<RequirementKind>('document');
	let questionType = $state('text');
	let optionText = $state('');
	let minValue = $state('');
	let maxValue = $state('');
	let requiresDocument = $state(false);
	let validFor = $state('');
	let reminder = $state('30');
	let kinds = $state<string[]>([]);
	let active = $state(true);
	let deleting = $state<RequirementListItem | null>(null);
	let deleteOpen = $state(false);
	let busy = $state(false);

	function openForm(r: RequirementListItem | null) {
		editing = r;
		name = r?.name ?? '';
		description = r?.description ?? '';
		kind = r?.kind ?? 'document';
		questionType = r?.question_type || 'text';
		optionText = r ? optionLines(r) : '';
		minValue = r?.min_value != null ? String(Number(r.min_value)) : '';
		maxValue = r?.max_value != null ? String(Number(r.max_value)) : '';
		requiresDocument = r?.requires_document ?? false;
		validFor = r?.valid_for_months ? String(r.valid_for_months) : '';
		reminder = String(r?.reminder_days_before ?? 30);
		kinds = [...(r?.blocked_event_kinds ?? [])];
		active = r?.is_active ?? true;
		formOpen = true;
	}
	function openDelete(r: RequirementListItem) {
		deleting = r;
		deleteOpen = true;
	}
	const toggleKind = (k: string, on: boolean) => (kinds = on ? [...kinds, k] : kinds.filter((x) => x !== k));
	const submit = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close();
		};
	};
	const err = (k: string) => (form?.reqAction === 'save' ? form.reqFields?.[k] : undefined);

	usePageHeader(() => ({ title: t('req.title'), context: data.requirements.length === 1 ? t('req.count_one') : t('req.count', { n: data.requirements.length }), actions }));
</script>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="button" onclick={() => openForm(null)}><Plus size={16} /> {t('req.add')}</button>
{/snippet}

<svelte:head><title>{pageTitle(t('req.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-6 py-2.5">
		<div class="flex-1"></div>
		<InfoTip label={t('req.info_label')} text={t('req.info')} align="right" />
	</div>

	<div class="grid gap-4 px-6 py-4">
		{#if data.error}
			<Alert kind="error">{t('req.load_failed')}</Alert>
		{:else if !data.requirements.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<FileCheck size={28} />
				<p>{t('req.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr>
							<th>{t('req.col.name')}</th><th class="hidden md:table-cell">{t('req.col.kind')}</th><th class="hidden lg:table-cell">{t('req.col.valid')}</th><th class="hidden xl:table-cell">{t('req.col.reminder')}</th><th class="hidden lg:table-cell">{t('req.col.blocks')}</th><th class="text-right">{t('req.col.used')}</th><th><span class="sr-only">{t('req.col.actions')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each data.requirements as r (r.id)}
							<tr class="hover:bg-paper {r.is_active ? '' : 'opacity-70'}">
								<td>
									<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
										<button type="button" class="link text-left font-semibold" onclick={() => openForm(r)}>{r.name}</button>
										{#if !r.is_active}<span class="badge badge-sm badge-warning">{t('req.inactive')}</span>{/if}
									</div>
									{#if r.description}<p class="text-sm text-muted">{r.description}</p>{/if}
								</td>
								<td class="hidden text-sm md:table-cell">
									<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
										{#if r.kind === 'question'}
											<span class="badge badge-sm badge-outline">{t('req.kind.question')}</span><span class="text-muted">{t(`req.qtype.${r.question_type}`)}</span>
										{:else}
											<span class="badge badge-sm badge-outline">{t('req.kind.document')}</span>{#if r.requires_document}<span class="text-muted">{t('req.with_file')}</span>{/if}
										{/if}
									</div>
								</td>
								<td class="hidden text-sm whitespace-nowrap lg:table-cell">{r.valid_for_months ? t('req.valid_months', { n: r.valid_for_months }) : t('req.valid_forever')}</td>
								<td class="hidden text-sm whitespace-nowrap xl:table-cell">{r.valid_for_months ? t('req.reminder_days', { n: r.reminder_days_before }) : '–'}</td>
								<td class="hidden lg:table-cell">
									{#if r.blocked_event_kinds.length}
										<div class="flex flex-wrap gap-1">{#each r.blocked_event_kinds as k (k)}<span class="badge badge-sm badge-outline">{t(`eventform.kind.${k}`)}</span>{/each}</div>
									{:else}<span class="text-sm text-muted">{t('req.blocks_none')}</span>{/if}
								</td>
								<td class="text-right font-mono text-xs">{r.usage_count}</td>
								<td class="text-right whitespace-nowrap">
									<div class="flex justify-end gap-1">
										<button class="btn btn-ghost btn-xs btn-square text-muted" type="button" aria-label={t('req.edit') + ': ' + r.name} onclick={() => openForm(r)}><ChevronRight size={16} /></button>
										<button class="btn btn-ghost btn-xs btn-square text-error" type="button" aria-label={t('req.delete_title') + ': ' + r.name} onclick={() => openDelete(r)}><Trash2 size={14} /></button>
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>

<Modal bind:open={formOpen} title={editing ? t('req.edit') : t('req.add')}>
	<form method="POST" action="?/save" class="grid gap-3" use:enhance={submit(() => (formOpen = false))}>
		<input type="hidden" name="id" value={editing?.id ?? ''} />
		{#if form?.reqAction === 'save' && form.reqError && !Object.keys(form.reqFields ?? {}).length}<Alert kind="error" title={t('req.save_failed')}>{form.reqError}</Alert>{/if}
		<Field label={t('req.f.name')} name="name" required bind:value={name} error={err('name')} autocomplete="off" />
		<div class="form-control">
			<label class="label" for="req-desc"><span class="label-text">{t('req.f.description')}</span></label>
			<textarea id="req-desc" name="description" class="textarea textarea-bordered w-full" rows="2" bind:value={description}></textarea>
			<span class="label-text-alt mt-1 block">{t('req.f.description_help')}</span>
		</div>
		<fieldset class="grid gap-1.5">
			<legend class="label-text mb-1">{t('req.f.kind')}</legend>
			<div class="flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
				<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="kind" value="document" bind:group={kind} disabled={!!editing?.usage_count} /> {t('req.kind.document')}</label>
				<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="kind" value="question" bind:group={kind} disabled={!!editing?.usage_count} /> {t('req.kind.question')}</label>
			</div>
			{#if editing?.usage_count}<input type="hidden" name="kind" value={kind} />{/if}
			<span class="label-text-alt">{editing?.usage_count ? t('req.f.kind_locked') : kind === 'question' ? t('req.f.kind_question_help') : t('req.f.kind_document_help')}</span>
			{#if err('kind')}<span class="block text-xs text-error">{err('kind')}</span>{/if}
		</fieldset>
		{#if kind === 'question'}
			<div class="form-control">
				<label class="label" for="req-qtype"><span class="label-text">{t('req.f.qtype')}</span></label>
				<select id="req-qtype" name="question_type" class="select select-bordered w-full {err('question_type') ? 'select-error' : ''}" bind:value={questionType}>
					{#each QUESTION_TYPES as q (q)}<option value={q}>{t(`req.qtype.${q}`)}</option>{/each}
				</select>
				{#if err('question_type')}<span class="mt-1 block text-xs text-error">{err('question_type')}</span>{/if}
			</div>
			{#if hasChoices(questionType)}
				<div class="form-control">
					<label class="label" for="req-options"><span class="label-text">{t('req.f.options')}</span></label>
					<textarea id="req-options" name="options" class="textarea textarea-bordered w-full {err('options') ? 'textarea-error' : ''}" rows="4" bind:value={optionText} placeholder={t('req.f.options_placeholder')}></textarea>
					<span class="label-text-alt mt-1 block">{err('options') || t('req.f.options_help')}</span>
				</div>
			{/if}
			{#if questionType === 'number'}
				<div class="grid gap-3 sm:grid-cols-2">
					<Field label={t('req.f.min')} name="min_value" type="number" step="any" bind:value={minValue} error={err('min_value')} />
					<Field label={t('req.f.max')} name="max_value" type="number" step="any" bind:value={maxValue} error={err('max_value')} />
				</div>
			{/if}
		{:else}
			<label class="label cursor-pointer justify-start gap-2 py-1">
				<input type="checkbox" name="requires_document" class="toggle" bind:checked={requiresDocument} />
				<span class="label-text">{t('req.f.requires_document')}</span>
			</label>
		{/if}
		<div class="grid gap-3 sm:grid-cols-2">
			<Field label={t('req.f.valid_for')} name="valid_for_months" type="number" min="1" max="120" bind:value={validFor} error={err('valid_for_months')} help={t('req.f.valid_for_help')} />
			<Field label={t('req.f.reminder')} name="reminder_days_before" type="number" min="0" max="365" bind:value={reminder} error={err('reminder_days_before')} help={t('req.f.reminder_help')} />
		</div>
		<fieldset class="grid gap-1.5">
			<legend class="label-text mb-1">{t('req.f.blocks')}</legend>
			<div class="grid grid-flow-col grid-cols-2 grid-rows-3 gap-x-4 gap-y-1.5">
				{#each EVENT_KINDS as k (k)}
					<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" name="blocked_event_kinds" value={k} checked={kinds.includes(k)} onchange={(e) => toggleKind(k, e.currentTarget.checked)} /> {t(`eventform.kind.${k}`)}</label>
				{/each}
			</div>
			<span class="label-text-alt">{t('req.f.blocks_help')}</span>
			{#if err('blocked_event_kinds')}<span class="block text-xs text-error">{err('blocked_event_kinds')}</span>{/if}
		</fieldset>
		<label class="label cursor-pointer justify-start gap-2 py-1">
			<input type="checkbox" name="is_active" class="toggle" bind:checked={active} />
			<span class="label-text">{t('req.f.active')}</span>
		</label>
		<p class="-mt-1 text-xs text-muted">{t('req.f.active_help')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (formOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('req.delete_title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={submit(() => (deleteOpen = false))}>
		<input type="hidden" name="id" value={deleting?.id ?? ''} />
		{#if form?.reqAction === 'delete' && form.reqError}<Alert kind="error" title={t('req.delete_failed')}>{form.reqError}</Alert>{/if}
		<p>{t('req.delete_body', { name: deleting?.name ?? '', n: deleting?.usage_count ?? 0 })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
