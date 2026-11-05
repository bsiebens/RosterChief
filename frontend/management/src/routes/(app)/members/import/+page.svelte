<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { ArrowLeft, Check, CircleAlert, CircleCheck, Download, FileSpreadsheet, Upload, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { ImportJob, ImportRow } from './+page.server.ts';

	let { data, form } = $props();
	let busy = $state(false);
	const fields = $derived((form && 'fields' in form ? form.fields : {}) as Record<string, string>);
	let filter = $state<'all' | 'new' | 'duplicate' | 'error'>('all');
	let fileName = $state('');

	// A started import is followed in the browser: poll the job until it is done or failed.
	let job = $state<ImportJob | null>(null);
	$effect(() => {
		job = form?.step === 'running' ? (form.job as ImportJob) : null;
	});
	$effect(() => {
		const id = job?.id;
		if (!id || job?.status === 'done' || job?.status === 'failed') return;
		const timer = setInterval(async () => {
			try {
				const res = await fetch(`/api/v1/members/import/jobs/${id}`, { headers: { accept: 'application/json' } });
				if (res.ok) job = (await res.json()) as ImportJob;
			} catch {
				// try again on the next tick
			}
		}, 1000);
		return () => clearInterval(timer);
	});
	const step = $derived(job?.status === 'done' ? 'done' : job ? 'running' : form?.step === 'preview' ? 'preview' : 'upload');
	const STEPS = ['upload', 'preview', 'done'] as const;
	const stepIndex = $derived(STEPS.indexOf(step === 'running' ? 'preview' : step));
	const preview = $derived(form?.step === 'preview' ? form.preview : null);
	const result = $derived(job?.status === 'done' ? job.result : null);
	const rows = $derived<ImportRow[]>((preview?.rows ?? result?.rows ?? []) as ImportRow[]);
	const shown = $derived(rows.filter((r) => filter === 'all' || r.status === filter || (filter === 'new' && r.status === 'created')));
	// Only the rows that will be created travel back: the server checks them again.
	const payload = $derived(JSON.stringify(rows.filter((r) => r.status === 'new')));
	const message = (r: ImportRow) => Object.values(r.errors ?? {}).flat().join(' ') || r.note;
	const pill: Record<string, string> = { new: 'badge-info', created: 'badge-success', duplicate: 'badge-ghost', error: 'badge-error' };
	const run = () => {
		busy = true;
		return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
			await update({ reset: false });
			busy = false;
			filter = 'all';
		};
	};

	usePageHeader(() => ({ title: t('import.title'), context: t('import.context'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/members"><ArrowLeft size={16} /> {t('import.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('import.title'))}</title></svelte:head>

<ol class="mb-4 flex flex-wrap items-center gap-2 text-sm" aria-label={t('import.steps')}>
	{#each STEPS as s, i (s)}
		{@const state = i < stepIndex ? 'done' : i === stepIndex ? 'current' : 'todo'}
		<li class="flex items-center gap-2 {state === 'current' ? 'font-bold text-ink' : state === 'done' ? 'font-semibold text-club-dark' : 'text-muted'}" aria-current={state === 'current' ? 'step' : undefined}>
			<span class="grid h-6 w-6 place-items-center rounded-full text-xs {state === 'current' ? 'bg-club text-club-content' : state === 'done' ? 'bg-club/15 text-club-dark' : 'bg-rule'}">
				{#if state === 'done'}<Check size={14} strokeWidth={3} aria-hidden="true" />{:else}{i + 1}{/if}
			</span>{t(`import.step.${s}`)}
			{#if i < STEPS.length - 1}<span class="mx-1 h-px w-8 {i < stepIndex ? 'bg-club/40' : 'bg-line'}"></span>{/if}
		</li>
	{/each}
</ol>

{#if step === 'upload'}
	<div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
		<form method="POST" action="?/preview" enctype="multipart/form-data" class="card card-body" use:enhance={run}>
			<h2 class="card-title"><FileSpreadsheet size={18} /> {t('import.upload.title')}</h2>
			{#if form?.error}<Alert kind="error" title={t('import.failed')}>{form.error}</Alert>{/if}
			<div class="form-control w-full">
				<label class="label" for="imp-file"><span class="label-text">{t('import.file')}</span></label>
				<input id="imp-file" name="file" type="file" accept=".csv,.xlsx,.txt" class="file-input w-full {fields.file ? 'file-input-error' : ''}" required onchange={(e) => (fileName = e.currentTarget.files?.[0]?.name ?? '')} />
				<span class="mt-1 block text-xs text-muted">{t('import.file_help')}</span>
			</div>
			<div class="form-control w-full">
				<label class="label" for="imp-season"><span class="label-text">{t('import.season')}</span></label>
				<select id="imp-season" name="season_id" class="select select-bordered w-full">
					<option value="">{t('import.season_none')}</option>
					{#each data.seasons as s (s.id)}<option value={s.id} selected={s.is_current}>{s.name}{s.is_current ? ` (${t('members.list.current')})` : ''}</option>{/each}
				</select>
				{#if fields.season_id}<span class="mt-1 block text-xs text-error">{fields.season_id}</span>{/if}
				<span class="mt-1 block text-xs text-muted">{t('import.season_help')}</span>
			</div>
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Upload size={16} /> {t('import.check')}</button>
			</div>
		</form>

		<div class="card card-body">
			<h2 class="card-title text-base">{t('import.columns.title')}</h2>
			<p class="text-sm text-muted">{t('import.columns.intro')}</p>
			<ul class="grid gap-1.5 text-sm">
				{#each ['first_name', 'last_name', 'email', 'phone', 'emergency_phone', 'date_of_birth', 'language', 'license_number', 'team', 'family', 'family_role'] as c (c)}
					<li class="flex items-baseline justify-between gap-3"><code class="font-mono text-xs text-ink">{c}</code><span class="text-right text-xs text-muted">{t(`import.col.${c}`)}</span></li>
				{/each}
			</ul>
			<a class="btn btn-outline btn-sm mt-2 gap-2" href="/members/import/template" download><Download size={14} /> {t('import.template')}</a>
			<p class="text-xs text-muted">{t('import.duplicates_note')}</p>
		</div>
	</div>
{:else if step === 'running'}
	<div class="card card-body max-w-2xl gap-3">
		<h2 class="card-title"><Users size={18} /> {t('import.running.title')}</h2>
		{#if job?.status === 'failed'}
			<Alert kind="error" title={t('import.running.failed')}>{job.error}</Alert>
			<a class="btn btn-outline self-start" href="/members/import">{t('import.again')}</a>
		{:else}
			<p class="text-sm text-muted">{t('import.running.body', { done: job?.processed ?? 0, total: job?.total ?? 0 })}</p>
			<progress class="progress w-full" value={job?.processed ?? 0} max={job?.total || 1}></progress>
			<p class="text-xs text-muted">{t('import.running.hint')}</p>
		{/if}
	</div>
{:else}
	{#if preview}
		<div class="mb-4 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line bg-line">
			{#each [['new', preview.counts.new, 'text-ink'], ['duplicate', preview.counts.duplicate, 'text-muted'], ['error', preview.counts.error, 'text-danger']] as [k, n, c] (k)}
				<button type="button" class="bg-white p-4 text-left hover:bg-surface {filter === k ? 'ring-2 ring-club ring-inset' : ''}" onclick={() => (filter = filter === k ? 'all' : (k as typeof filter))}>
					<span class="block font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t(`import.count.${k}`)}</span>
					<span class="block font-display text-3xl font-extrabold {c}">{n}</span>
				</button>
			{/each}
		</div>
	{:else if result}
		<div class="mb-4">
			<Alert kind={result.counts.error ? 'warning' : 'success'} title={t('import.done.title')}>
				{t('import.done.body', { created: result.counts.created, duplicate: result.counts.duplicate, error: result.counts.error })}
			</Alert>
		</div>
	{/if}

	<div class="card overflow-x-auto">
		<table class="table">
			<thead>
				<tr><th class="w-14">{t('import.th.line')}</th><th>{t('import.th.status')}</th><th>{t('import.th.name')}</th><th>{t('members.list.col.email')}</th><th>{t('import.th.team')}</th><th>{t('import.th.family')}</th><th>{t('import.th.remarks')}</th></tr>
			</thead>
			<tbody>
				{#each shown as r (r.line)}
					<tr class={r.status === 'error' ? 'bg-row-warn' : ''}>
						<td class="font-mono text-xs text-muted">{r.line}</td>
						<td><span class="badge badge-sm {pill[r.status]}">{t(`import.status.${r.status}`)}</span></td>
						<td class="font-semibold">{r.first_name} {r.last_name}</td>
						<td class="text-sm text-muted">{r.email || '—'}</td>
						<td class="text-sm">{r.team || '—'}</td>
						<td class="text-sm">{r.family ? `${r.family} (${r.family_role})` : '—'}</td>
						<td class="text-sm {r.status === 'error' ? 'text-danger' : 'text-muted'}">{message(r)}</td>
					</tr>
				{:else}
					<tr><td colspan="7" class="py-6 text-center text-muted">{t('import.none_shown')}</td></tr>
				{/each}
			</tbody>
		</table>
	</div>

	<div class="mt-4 flex flex-wrap items-center justify-end gap-2">
		{#if preview}
			<a class="btn btn-outline" href="/members/import">{t('import.again')}</a>
			<form method="POST" action="?/commit" use:enhance={run}>
				<input type="hidden" name="rows" value={payload} />
				<input type="hidden" name="season_id" value={preview.season_id ?? ''} />
				<button class="btn btn-primary gap-2" type="submit" disabled={busy || !preview.counts.new}><Users size={16} /> {t('import.confirm', { n: preview.counts.new })}</button>
			</form>
		{:else}
			<a class="btn btn-outline" href="/members/import">{t('import.another')}</a>
			<a class="btn btn-primary gap-2" href="/members"><CircleCheck size={16} /> {t('import.to_members')}</a>
		{/if}
	</div>
{/if}
