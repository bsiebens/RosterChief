<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { ArrowLeft, Lock, Save, Send, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import FieldBuilder from '#lib/components/forms/FieldBuilder.svelte';
	import FormDetailsFields from '#lib/components/forms/FormDetailsFields.svelte';
	import FormPreview from '#lib/components/forms/FormPreview.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import PanelTabs from '#lib/components/news/PanelTabs.svelte';
	import { draftsKey, draftsOf, draftToIn, sendFormHref, type FieldDraft, type FormIn } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.item);
	const formId = $props.id();

	const detailsOf = (f: typeof data.item): FormIn => ({ title: f.title, description: f.description, login_required: f.login_required, is_active: f.is_active });
	// svelte-ignore state_referenced_locally
	let v = $state<FormIn>(detailsOf(data.item));
	// svelte-ignore state_referenced_locally
	let fields = $state<FieldDraft[]>(draftsOf(data.item));
	// A reload after a save brings the stored form: start the editor from it again.
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
	const tabs = $derived([{ key: 'edit', label: t('forms.tab.edit') }, { key: 'preview', label: t('forms.tab.preview') }]);

	// Unsaved-changes guard for in-app navigation and for closing / reloading the tab.
	// Not while saving, and not for a refresh of this very page (the reload after a save is a same-page navigation).
	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('forms.unsaved_confirm'))) cancel();
	});

	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const hasResponses = $derived(!!form && 'code' in form && form.code === 'has_responses');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const errorText = $derived.by(() => {
		if (!form || !('status' in form) || !form.status || hasResponses) return '';
		if (form.message) return String(form.message);
		return form.status === 403 ? t('forms.err.forbidden') : t('forms.err.failed');
	});
	const valid = () => !!v.title.trim() && fields.every((f) => !f.is_active || (f.label.trim() && (!['choice', 'multichoice'].includes(f.field_type) || f.options.some((o) => o.label.trim()))));

	usePageHeader(() => ({ title: t('forms.title_edit'), context: data.item.title, actions }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/forms"><ArrowLeft size={16} /> {t('forms.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('forms.title_edit'))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created && !dirty}<Alert kind="success">{t('forms.created')}</Alert>{/if}
	{#if data.duplicated && !dirty}<Alert kind="success">{t('forms.duplicated')}</Alert>{/if}
	{#if done === 'save'}<Alert kind="success">{t('forms.saved')}</Alert>{/if}
	{#if done === 'deactivate'}<Alert kind="success">{t('forms.deactivated')}</Alert>{/if}
	{#if errorText}<Alert kind="error" title={t('forms.save_failed')}>{errorText}</Alert>{/if}
	{#if hasResponses}
		<Alert kind="warning" title={t('forms.delete.blocked_title')}>
			<p>{t('forms.delete.blocked', { title: item.title })}</p>
			{#if item.is_active}
				<form method="POST" action="?/deactivate" class="mt-2" use:enhance>
					<button class="btn btn-outline btn-sm" type="submit">{t('forms.delete.deactivate')}</button>
				</form>
			{/if}
		</Alert>
	{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<PanelTabs {tabs} bind:active={tab} label={t('forms.tab.label')} idBase={formId} />
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
						<h2 id="{formId}-details" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('forms.card.details')}</h2>
						<FormDetailsFields bind:v fields={errs} {tried} />
					</section>
					<section class="card card-body gap-3" aria-labelledby="{formId}-fields">
						<header class="grid gap-0.5">
							<h2 id="{formId}-fields" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('forms.card.fields')}</h2>
							<p class="text-xs text-muted">{t('forms.card.fields_desc')}</p>
						</header>
						{#if item.response_count}<Alert kind="info">{t('forms.fields.has_responses', { n: item.response_count })}</Alert>{/if}
						<FieldBuilder bind:fields errors={errs} {tried} />
					</section>
				</form>
			</div>
			<div id="{formId}-panel-preview" role="tabpanel" aria-labelledby="{formId}-tab-preview" hidden={tab !== 'preview'}>
				<p class="mb-3 text-sm text-muted">{t('forms.preview.help')}</p>
				<FormPreview title={v.title} description={v.description} {fields} />
			</div>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20" aria-labelledby="{formId}-side">
			<section class="card card-body gap-3">
				<h2 id="{formId}-side" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('forms.card.actions')}</h2>
				<div class="flex flex-wrap items-center gap-2 text-xs text-muted">
					<span class="badge badge-sm {item.is_active ? 'badge-success' : 'badge-warning'}">{item.is_active ? t('forms.active') : t('forms.inactive')}</span>
					{#if item.login_required}<span class="badge badge-sm gap-1"><Lock size={10} aria-hidden="true" /> {t('forms.login_required')}</span>{/if}
					<span class="font-mono">{t('forms.counts', { sends: item.send_count, responses: item.response_count })}</span>
				</div>
				{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('forms.unsaved')}</p>{/if}
				<button class="btn btn-primary w-full gap-2" type="submit" form={formId} disabled={busy || !dirty}><Save size={16} /> {t('common.save')}</button>
				{#if item.is_active && !dirty}
					<ActionList>
						<ActionRow icon={Send} chevron href={sendFormHref(item.id)}>{t('forms.send')}</ActionRow>
						<ActionRow icon={Trash2} danger disabled={busy} onclick={() => (deleteOpen = true)}>{t('common.delete')}</ActionRow>
					</ActionList>
				{:else}
					{#if item.is_active}<p class="text-xs text-muted">{t('forms.send_save_first')}</p>{/if}
					<ActionList><ActionRow icon={Trash2} danger disabled={busy} onclick={() => (deleteOpen = true)}>{t('common.delete')}</ActionRow></ActionList>
				{/if}
			</section>
		</aside>
	</div>
</div>

<Modal bind:open={deleteOpen} title={t('forms.delete.title')}>
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
		<p>{t('forms.delete.body', { title: item.title })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
