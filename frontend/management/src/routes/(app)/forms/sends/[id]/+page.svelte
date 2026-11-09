<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, CalendarClock, Check, Copy, Lock, LockOpen, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import ResponsesView from '#lib/components/forms/ResponsesView.svelte';
	import SendStatusBadge from '#lib/components/forms/SendStatusBadge.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import PanelTabs from '#lib/components/news/PanelTabs.svelte';
	import { isoToLocalInput } from '#lib/events/form-types.ts';
	import { formatDateTime } from '#lib/events/types.ts';
	import { formHref, type SendPatch } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { wallToIso } from '#lib/news.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const s = $derived(data.send);
	const id = $props.id();

	let tab = $state('responses');
	const tabs = $derived([{ key: 'responses', label: t('forms.sendtab.responses') }, { key: 'details', label: t('forms.sendtab.details') }]);
	let busy = $state(false);
	let editOpen = $state(false);
	let deleteOpen = $state(false);

	// Edit window
	let opens = $state('');
	let closes = $state('');
	let max = $state('');
	let isPublic = $state(false);
	$effect(() => {
		if (editOpen) {
			opens = isoToLocalInput(s.opens_at);
			closes = isoToLocalInput(s.closes_at);
			max = s.max_submissions_per_user ? String(s.max_submissions_per_user) : '';
			isPublic = s.is_public;
		}
	});
	const opensIso = $derived(wallToIso(opens));
	const closesIso = $derived(wallToIso(closes));
	const windowBad = $derived(!!opensIso && !!closesIso && closesIso <= opensIso);
	const maxNum = $derived(max.trim() === '' ? null : Math.floor(Number(max)));
	const maxBad = $derived(maxNum !== null && (!Number.isFinite(maxNum) || maxNum < 1));
	const patch = $derived(JSON.stringify({ opens_at: opensIso || null, closes_at: closesIso || null, max_submissions_per_user: maxNum, is_public: isPublic } satisfies SendPatch));

	const fullUrl = $derived(s.public_url_path ? `${data.origin}${s.public_url_path}` : '');
	let copied = $state(false);
	async function copy() {
		try {
			await navigator.clipboard.writeText(fullUrl);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			copied = false;
		}
	}

	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const hasResponses = $derived(!!form && 'code' in form && form.code === 'has_responses');
	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const errorText = $derived.by(() => {
		if (!form || !('status' in form) || !form.status) return '';
		if (hasResponses) return t('forms.send.delete_blocked');
		return form.message ? String(form.message) : t('forms.err.failed');
	});
	const plain = () => {
		busy = true;
		return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
			await update({ reset: false });
			busy = false;
		};
	};
	const isClosed = $derived(s.status === 'closed');

	usePageHeader(() => ({ title: t('forms.sends.detail'), context: data.send.form_title, badge, actions }));
</script>

{#snippet badge()}<SendStatusBadge status={s.status} />{/snippet}
{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/forms/sends"><ArrowLeft size={16} /> {t('forms.sends.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('forms.sends.detail'))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created}<Alert kind="success">{t('forms.send.created')}</Alert>{/if}
	{#if done === 'update'}<Alert kind="success">{t('forms.send.updated')}</Alert>{/if}
	{#if done === 'close'}<Alert kind="success">{t('forms.send.closed')}</Alert>{/if}
	{#if done === 'reopen'}<Alert kind="success">{t('forms.send.reopened')}</Alert>{/if}
	{#if done === 'deleteSubmission'}<Alert kind="success">{t('forms.resp.deleted')}</Alert>{/if}
	{#if errorText && form && 'op' in form && form.op !== 'update'}<Alert kind="error" title={t('forms.action_failed')}>{errorText}</Alert>{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<PanelTabs {tabs} bind:active={tab} label={t('forms.sendtab.label')} idBase={id} />
			<div id="{id}-panel-responses" role="tabpanel" aria-labelledby="{id}-tab-responses" hidden={tab !== 'responses'}>
				{#if data.report}
					<ResponsesView report={data.report} sendId={s.id} />
				{:else}
					<Alert kind="error">{t('forms.resp.load_failed')}</Alert>
				{/if}
			</div>
			<div id="{id}-panel-details" role="tabpanel" aria-labelledby="{id}-tab-details" hidden={tab !== 'details'}>
				<section class="card card-body gap-4">
					<dl class="grid gap-3 text-sm sm:grid-cols-2">
						<div><dt class="text-xs text-muted">{t('forms.sends.col.form')}</dt><dd><a class="link font-semibold" href={formHref(s.form_id)}>{s.form_title}</a></dd></div>
						<div><dt class="text-xs text-muted">{t('forms.sends.col.audience')}</dt><dd>{s.club_wide ? t('forms.audience.club_wide') : s.audience_label}</dd></div>
						<div><dt class="text-xs text-muted">{t('forms.send.opens')}</dt><dd>{s.opens_at ? formatDateTime(s.opens_at) : t('forms.send.immediately')}</dd></div>
						<div><dt class="text-xs text-muted">{t('forms.send.closes')}</dt><dd>{s.closes_at ? formatDateTime(s.closes_at) : t('forms.send.never')}</dd></div>
						<div><dt class="text-xs text-muted">{t('forms.send.max')}</dt><dd>{s.max_submissions_per_user ?? t('forms.send.unlimited')}</dd></div>
						<div><dt class="text-xs text-muted">{t('forms.send.sent_by')}</dt><dd>{s.created_by?.name ?? t('common.none')} · {formatDateTime(s.created)}</dd></div>
						{#if s.season}<div><dt class="text-xs text-muted">{t('forms.send.season')}</dt><dd>{s.season.name}</dd></div>{/if}
						{#if s.reminder_sent_at}<div><dt class="text-xs text-muted">{t('forms.send.reminder')}</dt><dd>{formatDateTime(s.reminder_sent_at)}</dd></div>{/if}
					</dl>
					{#each [[t('forms.audience.teams'), s.teams], [t('forms.audience.groups'), s.groups], [t('forms.audience.invited'), s.invited_members], [t('forms.audience.excluded'), s.excluded_members]] as [label, list] (label)}
						{#if Array.isArray(list) && list.length}
							<div class="grid gap-1">
								<h3 class="font-display text-xs font-bold tracking-wide text-ink uppercase">{label}</h3>
								<ul class="flex flex-wrap gap-1.5">{#each list as r (r.id)}<li class="rounded-full border border-line bg-subhead px-3 py-0.5 text-sm">{r.name}</li>{/each}</ul>
							</div>
						{/if}
					{/each}
				</section>
			</div>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20" aria-labelledby="{id}-side">
			<section class="card card-body gap-3">
				<h2 id="{id}-side" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('forms.sends.card.status')}</h2>
				<div class="flex flex-wrap items-center gap-2"><SendStatusBadge status={s.status} /><span class="font-mono text-xs text-muted">{t('forms.sends.x_of_y', { x: s.response_count, y: s.audience_count })}</span></div>
				{#if s.opens_at || s.closes_at}
					<p class="text-xs text-muted">
						{#if s.opens_at}{t('forms.sends.from', { date: formatDateTime(s.opens_at) })}{/if}
						{#if s.opens_at && s.closes_at}{' '}·{' '}{/if}
						{#if s.closes_at}{t('forms.sends.until', { date: formatDateTime(s.closes_at) })}{/if}
					</p>
				{/if}

				<div class="grid gap-2">
					{#if isClosed || s.status === 'inactive'}
						<form method="POST" action="?/reopen" use:enhance={plain}>
							<button class="btn btn-outline w-full gap-2" type="submit" disabled={busy}><LockOpen size={16} /> {t('forms.send.reopen')}</button>
						</form>
					{:else}
						<form method="POST" action="?/close" use:enhance={plain}>
							<button class="btn btn-outline w-full gap-2" type="submit" disabled={busy}><Lock size={16} /> {t('forms.send.close')}</button>
						</form>
					{/if}
					<button class="btn btn-outline gap-2" type="button" onclick={() => (editOpen = true)}><CalendarClock size={16} /> {t('forms.send.edit_window')}</button>
					<button class="btn btn-outline btn-error gap-2" type="button" onclick={() => (deleteOpen = true)}><Trash2 size={16} /> {t('common.delete')}</button>
				</div>
			</section>

			<section class="card card-body gap-2" aria-labelledby="{id}-pub">
				<h2 id="{id}-pub" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('forms.send.public_link')}</h2>
				{#if s.is_public && fullUrl}
					<div class="flex items-center gap-2">
						<input class="input input-bordered input-sm min-w-0 flex-1 font-mono text-xs" readonly value={fullUrl} aria-label={t('forms.send.public_link')} onfocus={(e) => e.currentTarget.select()} />
						<button class="btn btn-outline btn-sm gap-1" type="button" onclick={copy}>{#if copied}<Check size={14} /> {t('forms.send.copied')}{:else}<Copy size={14} /> {t('forms.send.copy')}{/if}</button>
					</div>
					<p class="text-xs text-muted">{t('forms.send.public_on_help')}</p>
				{:else}
					<p class="text-sm text-muted">{t('forms.send.public_off')}</p>
				{/if}
			</section>
		</aside>
	</div>
</div>

<Modal bind:open={editOpen} title={t('forms.send.edit_window')}>
	<form
		method="POST"
		action="?/update"
		class="grid gap-4"
		use:enhance={({ cancel }) => {
			if (windowBad || maxBad) {
				cancel();
				return;
			}
			busy = true;
			return async ({ update, result }) => {
				await update({ reset: false });
				busy = false;
				if (result.type === 'success') editOpen = false;
			};
		}}>
		<input type="hidden" name="payload" value={patch} />
		{#if errorText && form && 'op' in form && form.op === 'update'}<Alert kind="error">{errorText}</Alert>{/if}
		<Field label={t('forms.send.opens')} type="datetime-local" bind:value={opens} help={t('forms.send.opens_help')} error={errs.opens_at} />
		<Field label={t('forms.send.closes')} type="datetime-local" bind:value={closes} help={t('forms.send.closes_help')} error={errs.closes_at || (windowBad ? t('forms.err.window') : '')} />
		<Field label={t('forms.send.max')} type="number" min="1" step="1" bind:value={max} help={t('forms.send.max_help')} error={errs.max_submissions_per_user || (maxBad ? t('forms.err.max') : '')} />
		<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={isPublic} />
			<span class="grid gap-0.5"><span class="text-sm font-semibold text-ink">{t('forms.send.public')}</span><span class="text-xs text-muted">{errs.is_public || t('forms.send.public_help')}</span></span>
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (editOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('forms.send.delete_title')}>
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
		<p>{t('forms.send.delete_body', { title: s.form_title })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
