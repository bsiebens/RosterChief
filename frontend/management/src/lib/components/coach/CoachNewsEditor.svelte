<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { ArrowLeft, Camera, EyeOff, ImagePlus, Save, Send, Trash2, Undo2, Upload, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import NewsPhotos from '#lib/components/news/NewsPhotos.svelte';
	import RichTextEditor from '#lib/components/news/RichTextEditor.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { isoToLocalInput } from '#lib/events/form-types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { blankNews, coachPill, NEWS_VISIBILITIES, newsToForm, wallToIso, type NewsDetail, type NewsIn, type NewsPhoto, type TeamOption } from '#lib/news.ts';

	/**
	 * The coach app's news editor (phone first): one column, big touch targets and a sticky action bar above the tab bar.
	 * `item` is null when writing a new post. Saving, submitting and publishing all end back on the coach News tab with a
	 * short confirmation. A coach who may only add news saves drafts and submits them for review; a publisher publishes or schedules.
	 */
	let { item, teams, canPublishNew = false }: { item: NewsDetail | null; teams: TeamOption[]; canPublishNew?: boolean } = $props();

	const BACK = '/coach?tab=news';
	// svelte-ignore state_referenced_locally
	let v = $state<NewsIn>(item ? newsToForm(item) : blankNews());
	// svelte-ignore state_referenced_locally
	let photos = $state<NewsPhoto[]>(item ? ($state.snapshot(item.photos) as NewsPhoto[]) : []);
	// svelte-ignore state_referenced_locally
	let id = $state<string | null>(item?.id ?? null);
	// Fresh data from the server (after a failed step or another tab) starts the form again from what is stored.
	$effect(() => {
		const stored = item;
		untrack(() => {
			if (!stored) return;
			v = newsToForm(stored);
			photos = $state.snapshot(stored.photos) as NewsPhoto[];
		});
	});

	const canEdit = $derived(item ? item.can_edit : true);
	const canPublish = $derived(item ? item.can_publish : canPublishNew);
	const status = $derived(item?.status ?? 'draft');
	const key = (n: NewsIn) => JSON.stringify({ ...n, team_ids: [...n.team_ids].sort() });
	const dirty = $derived(!item || key(v) !== key(newsToForm(item)));
	const pill = $derived(coachPill(item ?? { status: 'draft', is_scheduled: false }));
	const when = $derived(item?.published_at ? formatDateTime(item.published_at) : '');
	const stateText = $derived(
		!item || status === 'draft' ? t('news.workflow.draft') : status === 'pending_review' ? t('news.workflow.pending_review') : t(item.is_scheduled ? 'news.workflow.scheduled' : 'news.workflow.published', { date: when })
	);

	let busy = $state(false);
	let tried = $state(false);
	let error = $state('');
	let fieldErrors = $state<Record<string, string>>({});
	const toggleTeam = (team: string) => (v.team_ids = v.team_ids.includes(team) ? v.team_ids.filter((x) => x !== team) : [...v.team_ids, team]);
	const err = $derived({ title: fieldErrors.title || (tried && !v.title.trim() ? t('news.err.title_required') : ''), body: fieldErrors.body || (tried && !v.body.trim() ? t('news.err.body_required') : '') });

	// Photos picked while writing a new post are uploaded right after it is saved.
	let pending = $state<{ file: File; url: string }[]>([]);
	let picker: HTMLInputElement | undefined = $state();
	function pick() {
		const files = picker?.files ? [...picker.files] : [];
		pending = [...pending, ...files.map((file) => ({ file, url: URL.createObjectURL(file) }))];
		if (picker) picker.value = '';
	}
	function unpick(entry: { file: File; url: string }) {
		URL.revokeObjectURL(entry.url);
		pending = pending.filter((x) => x !== entry);
	}
	onDestroy(() => pending.forEach((entry) => URL.revokeObjectURL(entry.url)));

	let publishOpen = $state(false);
	let at = $state('');
	let notify = $state(false);
	let confirm = $state<'delete' | 'unpublish' | null>(null);
	const iso = $derived(wallToIso(at));
	const future = $derived(!!iso && new Date(iso) > new Date());
	function openPublish() {
		tried = true;
		if (!v.title.trim() || !v.body.trim()) return;
		at = item?.published_at && new Date(item.published_at) > new Date() ? isoToLocalInput(item.published_at) : '';
		publishOpen = true;
	}

	const fail = (res: { status: number; message?: string; fields: Record<string, string> }) => {
		fieldErrors = res.fields;
		error = res.fields.__all__ || res.message || (res.status === 403 ? t('news.err.forbidden') : t('news.err.failed'));
		busy = false;
		return false;
	};

	/** Save what was typed (creating the post first when it is new), then run the workflow step, then go back to the list. */
	async function run(op: 'save' | 'submit-for-review' | 'publish', notice: string) {
		tried = true;
		error = '';
		fieldErrors = {};
		if (!v.title.trim() || !v.body.trim()) return;
		busy = true;
		const body = { ...v };
		if (!id) {
			const res = await api<NewsDetail>('POST', '/news', { body });
			if (!res.ok || !res.data) return fail(res);
			id = res.data.id;
		} else if (dirty) {
			const res = await api<NewsDetail>('PATCH', `/news/${id}`, { body });
			if (!res.ok) return fail(res);
		}
		while (pending.length) {
			const form = new FormData();
			form.append('image', pending[0].file);
			const res = await api<NewsPhoto>('POST', `/news/${id}/photos`, { form });
			if (!res.ok) return fail({ ...res, message: res.fields.image || res.message });
			unpick(pending[0]);
		}
		if (op !== 'save') {
			const res = await api<NewsDetail>('POST', `/news/${id}/${op}`, { body: op === 'publish' ? { published_at: iso || null, notify } : undefined });
			if (!res.ok) return fail(res);
			if (op === 'publish') notice = future ? 'scheduled' : 'published';
		}
		await leave(notice);
	}

	async function step(op: 'send-back' | 'unpublish', notice: string) {
		busy = true;
		error = '';
		const res = await api<NewsDetail>('POST', `/news/${id}/${op}`);
		if (!res.ok) return fail(res);
		confirm = null;
		await leave(notice);
	}
	async function remove() {
		busy = true;
		error = '';
		const res = await api('DELETE', `/news/${id}`);
		if (!res.ok) return fail(res);
		confirm = null;
		await leave('deleted');
	}
	/** Back to the coach News tab, replacing the editor in the history so the browser's back button does not return to it. */
	async function leave(notice: string) {
		await invalidateAll();
		await goto(`${BACK}&notice=${notice}`, { replaceState: true });
		busy = false;
	}

	usePageHeader(() => ({ title: t(item ? 'coach.edit_post' : 'coach.add_post'), context: item?.title || undefined }));
</script>

<svelte:head><title>{t(item ? 'coach.edit_post' : 'coach.add_post')}</title></svelte:head>

<div class="grid w-full max-w-2xl min-w-0 grid-cols-[minmax(0,1fr)] gap-3">
	<div class="flex items-center justify-between gap-2">
		<a class="btn btn-outline min-h-11 gap-2" href={BACK}><ArrowLeft size={16} aria-hidden="true" />{t('shell.nav.news')}</a>
		<span class="badge {pill[1]}">{t(pill[0])}</span>
	</div>

	{#if error}<Alert kind="error" title={t('news.save_failed')}>{error}</Alert>{/if}
	{#if item && !canEdit}<Alert kind="info">{status === 'published' ? t('news.locked') : t('news.read_only')}</Alert>{:else}<p class="text-sm text-muted">{stateText}</p>{/if}

	<fieldset class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3" disabled={!canEdit || busy}>
		<section class="card card-body gap-4 p-3.5">
			<Field class="input input-bordered min-h-12 w-full text-base" label={t('news.f.title')} bind:value={v.title} error={err.title} required maxlength={255} />
			<div class="w-full min-w-0">
				<RichTextEditor bind:value={v.body} label={t('news.f.body')} lang="nl" invalid={!!err.body} required rows={10} disabled={!canEdit || busy} />
				{#if err.body}<span class="mt-1 block text-xs text-error">{err.body}</span>{/if}
			</div>
			<details class="rounded-lg border border-line" open={!!(v.title_en || v.body_en)}>
				<summary class="flex min-h-12 cursor-pointer items-center px-3 font-display text-sm font-bold tracking-wide text-ink uppercase">{t('coach.news_english')}</summary>
				<div class="grid gap-4 border-t border-line p-3">
					<Field class="input input-bordered min-h-12 w-full text-base" label={t('news.f.title_en')} bind:value={v.title_en} error={fieldErrors.title_en} help={t('news.f.title_en_help')} maxlength={255} />
					<div class="w-full min-w-0">
						<RichTextEditor bind:value={v.body_en} label={t('news.f.body_en')} lang="en" invalid={!!fieldErrors.body_en} rows={8} disabled={!canEdit || busy} />
						<span class="label-text-alt mt-1 block">{t('news.f.body_en_help')}</span>
					</div>
				</div>
			</details>
		</section>

		{#if id && item}
			<NewsPhotos newsId={id} bind:photos {canEdit} touch />
		{:else}
			<section class="card card-body gap-3 p-3.5">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Camera size={16} aria-hidden="true" /> {t('news.card.photos')}{#if pending.length}<span class="font-mono text-xs font-normal text-muted">· {pending.length}</span>{/if}</h2>
				{#if pending.length}
					<ul class="grid grid-cols-3 gap-2">
						{#each pending as entry (entry.url)}
							<li class="relative">
								<img class="aspect-square w-full rounded-lg border border-line object-cover" src={entry.url} alt="" />
								<button type="button" class="btn btn-square btn-sm absolute top-1 right-1 min-h-9 min-w-9 bg-(--portal-panel)" aria-label={t('news.photos.delete')} onclick={() => unpick(entry)}><X size={16} aria-hidden="true" /></button>
							</li>
						{/each}
					</ul>
				{/if}
				<label class="btn btn-outline min-h-12 w-full cursor-pointer gap-2"><ImagePlus size={18} aria-hidden="true" />{t('news.photos.add')}<input bind:this={picker} type="file" accept="image/*" multiple class="sr-only" onchange={pick} /></label>
				<p class="text-xs text-muted">{t('coach.news_photos_first')}</p>
			</section>
		{/if}

		<section class="card card-body gap-4 p-3.5">
			<fieldset class="grid gap-2">
				<legend class="mb-1 font-display text-sm font-bold tracking-wide text-ink uppercase">{t('news.visibility')}</legend>
				<div class="grid gap-2">
					{#each NEWS_VISIBILITIES as option (option)}
						<label class="flex min-h-12 cursor-pointer items-center gap-3 rounded-lg border px-3 text-sm has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-club {v.visibility === option ? 'border-primary bg-subhead font-semibold' : 'border-line'}">
							<input class="radio radio-sm" type="radio" value={option} bind:group={v.visibility} />{t(`news.visibility.${option}`)}
						</label>
					{/each}
				</div>
				<span class="label-text-alt">{fieldErrors.visibility || t('news.visibility_help')}</span>
			</fieldset>
			<fieldset class="grid gap-2">
				<legend class="mb-1 font-display text-sm font-bold tracking-wide text-ink uppercase">{t('news.f.teams')}{#if v.team_ids.length}<span class="ml-2 font-mono text-xs font-normal text-muted normal-case">· {v.team_ids.length}</span>{/if}</legend>
				<div class="grid max-h-80 gap-1 overflow-y-auto rounded-lg border p-1.5 {fieldErrors.team_ids ? 'border-danger' : 'border-line'}">
					{#each teams as team (team.id)}
						<label class="flex min-h-11 min-w-0 cursor-pointer items-center gap-3 rounded-md px-2 text-sm hover:bg-surface">
							<input type="checkbox" class="checkbox" checked={v.team_ids.includes(team.id)} onchange={() => toggleTeam(team.id)} />
							<span class="truncate">{team.name}</span>
						</label>
					{/each}
				</div>
				<span class="label-text-alt {fieldErrors.team_ids ? 'text-error' : ''}">{fieldErrors.team_ids || (v.team_ids.length ? t('news.f.teams_help') : t('news.f.teams_none'))}</span>
			</fieldset>
		</section>
	</fieldset>

	{#if item && ((canPublish && (status === 'published' || status === 'pending_review')) || item.can_delete)}
		<section class="card card-body gap-2 p-3.5">
			{#if canPublish && status === 'pending_review'}
				<button type="button" class="btn btn-outline min-h-12 w-full gap-2" disabled={busy} onclick={() => step('send-back', 'sent_back')}><Undo2 size={16} aria-hidden="true" />{t('news.send_back')}</button>
				<p class="text-xs text-muted">{t('news.send_back_help')}</p>
			{/if}
			{#if canPublish && status === 'published'}
				<button type="button" class="btn btn-outline min-h-12 w-full gap-2" disabled={busy} onclick={() => (confirm = 'unpublish')}><EyeOff size={16} aria-hidden="true" />{t('news.unpublish')}</button>
			{/if}
			{#if item.can_delete}
				<button type="button" class="btn btn-outline btn-error min-h-12 w-full gap-2" disabled={busy} onclick={() => (confirm = 'delete')}><Trash2 size={16} aria-hidden="true" />{t('news.delete')}</button>
			{/if}
		</section>
	{/if}

	{#if canEdit}
		<div class="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-10 flex items-center gap-2 rounded-box border border-line bg-(--portal-panel) p-2.5 shadow-sm md:bottom-2">
			{#if canPublish}
				<button type="button" class="btn {status === 'published' ? 'btn-primary' : 'btn-outline'} min-h-12 flex-1 gap-2" disabled={busy || !dirty} onclick={() => run('save', 'saved')}><Save size={16} aria-hidden="true" />{status === 'published' ? t('news.save') : t('news.save_draft')}</button>
				{#if status !== 'published' || item?.is_scheduled}
					<button type="button" class="btn btn-primary min-h-12 flex-1 gap-2" disabled={busy} onclick={openPublish}><Upload size={16} aria-hidden="true" />{t('news.publish')}</button>
				{/if}
			{:else if status === 'draft'}
				<button type="button" class="btn btn-outline min-h-12 flex-1 gap-2" disabled={busy || !dirty} onclick={() => run('save', 'saved')}><Save size={16} aria-hidden="true" />{t('news.save_draft')}</button>
				<button type="button" class="btn btn-primary min-h-12 flex-1 gap-2" disabled={busy} onclick={() => run('submit-for-review', 'submitted')}><Send size={16} aria-hidden="true" />{t('news.submit')}</button>
			{:else}
				<button type="button" class="btn btn-primary min-h-12 flex-1 gap-2" disabled={busy || !dirty} onclick={() => run('save', 'saved')}><Save size={16} aria-hidden="true" />{t('news.save')}</button>
			{/if}
		</div>
		{#if !canPublish && status === 'draft'}<p class="text-center text-xs text-muted">{t('news.submit_help')}</p>{/if}
	{/if}
</div>

<Sheet open={publishOpen} title={t('news.publish.title')} onclose={() => (publishOpen = false)}>
	<form class="grid gap-4 p-4 pt-0" onsubmit={(e) => { e.preventDefault(); publishOpen = false; void run('publish', 'published'); }}>
		<Field class="input input-bordered min-h-12 w-full text-base" label={t('news.publish.when')} type="datetime-local" bind:value={at} help={t('news.publish.when_help')} />
		<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={notify} />
			<span class="grid gap-0.5"><span class="text-sm font-semibold text-ink">{t('news.publish.notify')}</span><span class="text-xs text-muted">{t('news.publish.notify_help')}</span></span>
		</label>
		<button class="btn btn-primary min-h-12 gap-2" type="submit" disabled={busy}><Upload size={16} aria-hidden="true" />{future ? t('news.publish.schedule') : t('news.publish.go')}</button>
	</form>
</Sheet>

<Sheet open={confirm !== null} title={confirm === 'delete' ? t('news.delete.title') : t('news.unpublish.title')} onclose={() => (confirm = null)}>
	{#if confirm}
		<div class="grid gap-4 p-4 pt-0">
			<p>{confirm === 'delete' ? t('news.delete.body', { title: item?.title ?? '' }) : t('news.unpublish.body', { title: item?.title ?? '' })}</p>
			<div class="grid grid-cols-2 gap-2">
				<button class="btn btn-outline min-h-12" type="button" onclick={() => (confirm = null)}>{t('common.cancel')}</button>
				{#if confirm === 'delete'}
					<button class="btn btn-error min-h-12 gap-2" type="button" disabled={busy} onclick={remove}><Trash2 size={16} aria-hidden="true" />{t('news.delete')}</button>
				{:else}
					<button class="btn btn-warning min-h-12 gap-2" type="button" disabled={busy} onclick={() => step('unpublish', 'unpublished')}><EyeOff size={16} aria-hidden="true" />{t('news.unpublish')}</button>
				{/if}
			</div>
		</div>
	{/if}
</Sheet>
