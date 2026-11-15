<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { Users, Workflow, ArrowLeft, EyeOff, Save, Send, Trash2, Undo2, Upload } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import NewsFields from '#lib/components/news/NewsFields.svelte';
	import NewsPhotos from '#lib/components/news/NewsPhotos.svelte';
	import NewsPreview from '#lib/components/news/NewsPreview.svelte';
	import PanelTabs from '#lib/components/news/PanelTabs.svelte';
	import NewsPublishModal from '#lib/components/news/NewsPublishModal.svelte';
	import NewsStatusBadge from '#lib/components/news/NewsStatusBadge.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { newsToForm, type NewsIn, type NewsPhoto } from '#lib/news.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.item);
	const formId = $props.id();

	// svelte-ignore state_referenced_locally
	let v = $state<NewsIn>(newsToForm(data.item));
	// svelte-ignore state_referenced_locally
	let photos = $state<NewsPhoto[]>($state.snapshot(data.item.photos));
	// A reload after a save or workflow step brings the stored item: start the form from it again.
	$effect(() => {
		const stored = data.item;
		untrack(() => {
			v = newsToForm(stored);
			photos = $state.snapshot(stored.photos) as NewsPhoto[];
		});
	});

	const key = (n: NewsIn) => JSON.stringify({ ...n, team_ids: [...n.team_ids].sort() });
	const dirty = $derived(key(v) !== key(newsToForm(item)));
	const canEdit = $derived(item.can_edit);
	const payload = $derived(canEdit ? JSON.stringify(v) : '');

	let tried = $state(false);
	let busy = $state(false);
	let tab = $state('edit');
	const tabs = $derived([{ key: 'edit', label: t('news.tab.edit') }, { key: 'preview', label: t('news.tab.preview') }]);
	let publishOpen = $state(false);
	let unpublishOpen = $state(false);
	let deleteOpen = $state(false);

	const fields = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const errorText = $derived.by(() => {
		if (!form || !('status' in form) || !form.status) return '';
		if (form.message) return String(form.message);
		return form.status === 403 ? t('news.err.forbidden') : form.status === 400 ? t('news.err.invalid') : t('news.err.failed');
	});
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const showFieldErrors = $derived(Object.keys(fields).length > 0);

	const teamNames = $derived(data.teams.filter((x) => v.team_ids.includes(x.id)).map((x) => x.short_name || x.name));
	const main = $derived(photos.find((p) => p.is_main) ?? photos[0] ?? null);

	const isPublished = $derived(item.status === 'published');
	const when = $derived(item.published_at ? formatDateTime(item.published_at) : '');
	const stateText = $derived(
		item.status === 'draft' ? t('news.workflow.draft') : item.status === 'pending_review' ? t('news.workflow.pending_review') : t(item.is_scheduled ? 'news.workflow.scheduled' : 'news.workflow.published', { date: when })
	);

	/** Validate before any request that sends the form's content; keeps the dialog flow off the server for empty fields. */
	const guard = ({ cancel, action }: { cancel: () => void; action: URL }) => {
		const sends = canEdit && !action.search.includes('delete');
		if (sends && (!v.title.trim() || !v.body.trim())) {
			tried = true;
			cancel();
			return;
		}
		busy = true;
	};

	usePageHeader(() => ({ title: canEdit ? t('news.title_edit') : t('news.title_view'), context: data.item.title, badge, actions }));
</script>

{#snippet badge()}<NewsStatusBadge {item} />{/snippet}
{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/news"><ArrowLeft size={16} /> {t('news.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(canEdit ? t('news.title_edit') : t('news.title_view'))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created && !dirty}<Alert kind="success">{t('news.created')}</Alert>{/if}
	{#if done === 'save'}<Alert kind="success">{t('news.saved')}</Alert>{/if}
	{#if errorText && !showFieldErrors}<Alert kind="error" title={t('news.action_failed')}>{errorText}</Alert>{/if}
	{#if errorText && showFieldErrors}<Alert kind="error" title={t('news.save_failed')}>{errorText}</Alert>{/if}
	{#if !canEdit}<Alert kind="info">{isPublished ? t('news.locked') : t('news.read_only')}</Alert>{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<PanelTabs {tabs} bind:active={tab} label={t('news.tab.label')} idBase={formId} />
			<div class="grid content-start gap-5" id="{formId}-panel-edit" role="tabpanel" aria-labelledby="{formId}-tab-edit" hidden={tab !== 'edit'}>
				<form
					id={formId}
					method="POST"
					action="?/save"
					novalidate
					use:enhance={(e) => {
						guard(e);
						return async ({ update }) => {
							await update({ reset: false });
							busy = false;
							tried = false;
						};
					}}>
					<input type="hidden" name="payload" value={payload} />
					<NewsFields bind:v teams={data.teams} {fields} {tried} disabled={!canEdit} />
				</form>

				<NewsPhotos newsId={item.id} bind:photos {canEdit} />
			</div>
			<div id="{formId}-panel-preview" role="tabpanel" aria-labelledby="{formId}-tab-preview" hidden={tab !== 'preview'}>
				<NewsPreview title={v.title} body={v.body} title_en={v.title_en} body_en={v.body_en} html={item.body_html} live={dirty} photoUrl={main?.url ?? null} focal={main ? { x: main.focal_x, y: main.focal_y } : null} author={item.created_by?.name ?? null} />
			</div>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20" aria-labelledby="{formId}-wf">
			<section class="card card-body gap-3">
				<h2 id="{formId}-wf" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Workflow size={16} class="shrink-0" aria-hidden="true" /> {t('news.card.workflow')}</h2>
				<div class="flex flex-wrap items-center gap-2"><NewsStatusBadge {item} /><span class="font-mono text-xs text-dim">{t(`news.visibility.short.${item.visibility}`)}</span></div>
				<p class="text-sm text-muted">{stateText}</p>
				<div class="grid gap-1.5">
					<p class="font-display text-xs font-bold tracking-[.08em] text-muted uppercase flex items-center gap-2"><Users size={16} class="shrink-0" aria-hidden="true" /> {t('news.card.audience')}</p>
					{#if teamNames.length}
						<div class="flex flex-wrap gap-1.5">{#each teamNames as name (name)}<span class="badge badge-neutral">{name}</span>{/each}</div>
					{:else}
						<span class="font-display text-xs font-bold tracking-[.14em] text-club uppercase">{t('news.club_wide')}</span>
					{/if}
				</div>
				{#if item.notified_at}<p class="font-mono text-[11px] text-dim">{t('news.workflow.notified', { date: formatDateTime(item.notified_at) })}</p>{/if}
				{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('news.unsaved')}</p>{/if}

				{#if canEdit}
					<button class="btn {item.can_publish || item.status !== 'draft' ? 'btn-primary' : 'btn-outline'} w-full gap-2" type="submit" form={formId} formaction="?/save" disabled={busy || !dirty}>
						<Save size={16} /> {item.status === 'published' ? t('news.save') : t('news.save_draft')}
					</button>
				{/if}
				{#if item.can_publish && item.status !== 'published'}
					<button class="btn btn-primary w-full gap-2" type="button" disabled={busy} onclick={() => ((tried = true), v.title.trim() && v.body.trim() && (publishOpen = true))}><Upload size={16} /> {t('news.publish')}</button>
				{:else if !item.can_publish && canEdit && item.status === 'draft'}
					<button class="btn btn-primary w-full gap-2" type="submit" form={formId} formaction="?/workflow" name="op" value="submit-for-review" disabled={busy}><Send size={16} /> {t('news.submit')}</button>
					<p class="text-xs text-muted">{t('news.submit_help')}</p>
				{/if}
				{#if (item.can_publish && (item.status === 'published' || item.status === 'pending_review')) || item.can_delete}
					<ActionList>
						{#if item.can_publish && item.status === 'published'}
							<ActionRow icon={EyeOff} disabled={busy} onclick={() => (unpublishOpen = true)}>{t('news.unpublish')}</ActionRow>
						{/if}
						{#if item.can_publish && item.status === 'pending_review'}
							<ActionRow icon={Undo2} type="submit" form={formId} formaction="?/workflow" name="op" value="send-back" disabled={busy}>{t('news.send_back')}</ActionRow>
						{/if}
						{#if item.can_delete}
							<ActionRow icon={Trash2} danger disabled={busy} onclick={() => (deleteOpen = true)}>{t('news.delete')}</ActionRow>
						{/if}
					</ActionList>
					{#if item.can_publish && item.status === 'pending_review'}<p class="text-xs text-muted">{t('news.send_back_help')}</p>{/if}
				{/if}
			</section>
		</aside>
	</div>
</div>

<NewsPublishModal bind:open={publishOpen} {payload} publishedAt={item.published_at} />

<Modal bind:open={unpublishOpen} title={t('news.unpublish.title')}>
	<form
		method="POST"
		action="?/workflow"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update({ reset: false });
				busy = false;
				unpublishOpen = false;
			};
		}}>
		<input type="hidden" name="op" value="unpublish" />
		<p>{t('news.unpublish.body', { title: item.title })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (unpublishOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-warning gap-2" type="submit" disabled={busy}><EyeOff size={16} /> {t('news.unpublish')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('news.delete.title')}>
	<form
		method="POST"
		action="?/delete"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ update, result }) => {
				await update({ reset: false });
				busy = false;
				if (result.type !== 'redirect') deleteOpen = false;
			};
		}}>
		<p>{t('news.delete.body', { title: item.title })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('news.delete')}</button>
		</div>
	</form>
</Modal>
