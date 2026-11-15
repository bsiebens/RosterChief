<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { ClipboardList, ListChecks, Settings2, ArrowLeft, ClipboardCheck, Presentation, Save, Table2, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import FieldBuilder from '#lib/components/forms/FieldBuilder.svelte';
	import FormPreview from '#lib/components/forms/FormPreview.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import PanelTabs from '#lib/components/news/PanelTabs.svelte';
	import { evaluateHref, meetingHref, resultsHref, type ChecklistDetail, type ChecklistIn } from '#lib/evaluations.ts';
	import { draftsKey, draftsOf, draftToIn, type FieldDraft, type FieldType, FIELD_TYPES } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.item);
	const formId = $props.id();
	// A rubric is scored in the app: no file uploads.
	const types: FieldType[] = FIELD_TYPES.filter((x) => x !== 'file');

	const detailsOf = (c: ChecklistDetail): ChecklistIn => ({ name: c.name, description: c.description, is_active: c.is_active });
	// svelte-ignore state_referenced_locally
	let v = $state<ChecklistIn>(detailsOf(data.item));
	// svelte-ignore state_referenced_locally
	let fields = $state<FieldDraft[]>(draftsOf(data.item));
	// A reload after a save brings the stored checklist: start the editor from it again.
	$effect(() => {
		const stored = data.item;
		untrack(() => {
			v = detailsOf(stored);
			fields = draftsOf(stored);
		});
	});

	const dirty = $derived(JSON.stringify(v) !== JSON.stringify(detailsOf(item)) || draftsKey(fields) !== draftsKey(draftsOf(item)));
	const payload = $derived(JSON.stringify({ details: v, fields: fields.map(draftToIn) }));

	let tried = $state(false);
	let busy = $state(false);
	let leaving = $state(false);
	let deleteOpen = $state(false);
	let tab = $state('edit');
	const tabs = $derived([{ key: 'edit', label: t('eval.tab.edit') }, { key: 'preview', label: t('eval.tab.preview') }]);

	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('forms.unsaved_confirm'))) cancel();
	});

	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const hasEvaluations = $derived(!!form && 'code' in form && form.code === 'has_evaluations');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const errorText = $derived.by(() => {
		if (!form || !('status' in form) || !form.status || hasEvaluations) return '';
		if (form.message) return String(form.message);
		return form.status === 403 ? t('common.error.forbidden') : t('common.error.generic');
	});
	const valid = () => !!v.name.trim() && fields.every((f) => !f.is_active || (f.label.trim() && (!['choice', 'multichoice'].includes(f.field_type) || f.options.some((o) => o.label.trim()))));

	usePageHeader(() => ({ title: t('eval.edit.title'), context: data.item.name, actions }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/evaluations"><ArrowLeft size={16} /> {t('eval.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('eval.edit.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created && !dirty}<Alert kind="success">{t('eval.created')}</Alert>{/if}
	{#if done === 'save'}<Alert kind="success">{t('eval.saved')}</Alert>{/if}
	{#if done === 'deactivate'}<Alert kind="success">{t('eval.deactivated')}</Alert>{/if}
	{#if errorText}<Alert kind="error" title={t('eval.save_failed')}>{errorText}</Alert>{/if}
	{#if hasEvaluations}
		<Alert kind="warning" title={t('eval.delete.blocked_title')}>
			<p>{t('eval.delete.blocked', { name: item.name })}</p>
			{#if item.is_active}
				<form method="POST" action="?/deactivate" class="mt-2" use:enhance>
					<button class="btn btn-outline btn-sm" type="submit">{t('eval.delete.deactivate')}</button>
				</form>
			{/if}
		</Alert>
	{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<PanelTabs {tabs} bind:active={tab} label={t('eval.tab.label')} idBase={formId} />
			<div class="grid content-start gap-5" id="{formId}-panel-edit" role="tabpanel" aria-labelledby="{formId}-tab-edit" hidden={tab !== 'edit'}>
				<form
					id={formId}
					method="POST"
					action="?/save"
					class="grid gap-5"
					novalidate
					use:enhance={({ cancel }) => {
						if (!valid()) {
							tried = true;
							cancel();
							return;
						}
						busy = true;
						leaving = true; // the save and the reload after it are not "leaving the page"
						return async ({ update }) => {
							await update({ reset: false });
							busy = false;
							leaving = false;
							tried = false;
						};
					}}>
					<input type="hidden" name="payload" value={payload} />
					<section class="card card-body gap-3" aria-labelledby="{formId}-details">
						<h2 id="{formId}-details" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ClipboardList size={16} class="shrink-0" aria-hidden="true" /> {t('eval.card.details')}</h2>
						<div class="form-control">
							<label class="label" for="{formId}-name"><span class="label-text">{t('eval.field.name')}</span></label>
							<input id="{formId}-name" class="input input-bordered w-full {errs.name || (tried && !v.name.trim()) ? 'input-error' : ''}" bind:value={v.name} maxlength="255" />
							{#if errs.name || (tried && !v.name.trim())}<span class="mt-1 block text-xs text-error">{errs.name || t('eval.err.name')}</span>{/if}
						</div>
						<div class="form-control">
							<label class="label" for="{formId}-desc"><span class="label-text">{t('eval.field.description')}</span></label>
							<textarea id="{formId}-desc" class="textarea textarea-bordered w-full" rows="2" bind:value={v.description}></textarea>
							<span class="label-text-alt mt-1 block">{t('eval.field.description_help')}</span>
						</div>
						<label class="flex w-fit cursor-pointer items-center gap-2 text-sm">
							<input type="checkbox" class="checkbox checkbox-sm" bind:checked={v.is_active} />
							{t('eval.field.active')}
						</label>
						<p class="-mt-1 text-xs text-muted">{t('eval.field.active_help')}</p>
					</section>
					<section class="card card-body gap-3" aria-labelledby="{formId}-fields">
						<header class="grid gap-0.5">
							<h2 id="{formId}-fields" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ListChecks size={16} class="shrink-0" aria-hidden="true" /> {t('eval.card.criteria')}</h2>
							<p class="text-xs text-muted">{t('eval.card.criteria_desc')}</p>
						</header>
						{#if item.evaluation_count}<Alert kind="info">{t('eval.criteria.has_evaluations', { n: item.evaluation_count })}</Alert>{/if}
						<FieldBuilder bind:fields errors={errs} {tried} {types} family />
					</section>
				</form>
			</div>
			<div id="{formId}-panel-preview" role="tabpanel" aria-labelledby="{formId}-tab-preview" hidden={tab !== 'preview'}>
				<p class="mb-3 text-sm text-muted">{t('eval.preview.help')}</p>
				<FormPreview title={v.name} description={v.description} {fields} />
			</div>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20" aria-labelledby="{formId}-side">
			<section class="card card-body gap-3">
				<h2 id="{formId}-side" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Settings2 size={16} class="shrink-0" aria-hidden="true" /> {t('eval.card.actions')}</h2>
				<div class="flex flex-wrap items-center gap-2 text-xs text-muted">
					<span class="badge badge-sm {item.is_active ? 'badge-success' : 'badge-warning'}">{item.is_active ? t('eval.active') : t('eval.inactive')}</span>
					<span class="font-mono">{t('eval.counts', { criteria: item.criteria_count, evaluations: item.evaluation_count })}</span>
				</div>
				{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('forms.unsaved')}</p>{/if}
				<button class="btn btn-primary w-full gap-2" type="submit" form={formId} disabled={busy || !dirty}><Save size={16} /> {t('common.save')}</button>
				<ActionList>
					{#if data.me?.can_execute_evaluations && !dirty}
						{#if item.is_active && item.criteria_count}<ActionRow icon={ClipboardCheck} chevron href={evaluateHref(item.id)}>{t('eval.evaluate')}</ActionRow>{/if}
						<ActionRow icon={Presentation} chevron href={meetingHref(item.id)}>{t('eval.meeting')}</ActionRow>
						<ActionRow icon={Table2} chevron href={resultsHref(item.id)}>{t('eval.results')}</ActionRow>
					{/if}
					<ActionRow icon={Trash2} danger disabled={busy} onclick={() => (deleteOpen = true)}>{t('common.delete')}</ActionRow>
				</ActionList>
			</section>
		</aside>
	</div>
</div>

<Modal bind:open={deleteOpen} title={t('eval.delete.title')}>
	<form
		method="POST"
		action="?/delete"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			leaving = true;
			return async ({ update, result }) => {
				await update({ reset: false });
				busy = false;
				if (result.type !== 'redirect') {
					leaving = false;
					deleteOpen = false;
				}
			};
		}}>
		<p>{t('eval.delete.body', { name: item.name })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
