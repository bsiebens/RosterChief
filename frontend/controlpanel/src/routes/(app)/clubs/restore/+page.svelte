<script lang="ts">
	import { goto } from '$app/navigation';
	import { tick } from 'svelte';
	import { FileArchive, ScanLine, Upload } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import RestoreReport, { type Report } from '#lib/components/backup/RestoreReport.svelte';

	type Preview = { version: number; name: string; slug: string; suggested_slug?: string; accounts: number; records: number; files: number; warnings: string[]; report: Report };
	type Restored = { id: string; slug: string; new_accounts: number; warnings: string[] };

	let file = $state<File | null>(null);
	let preview = $state<Preview | null>(null);
	let restored = $state<Restored | null>(null);
	let name = $state('');
	let slug = $state('');
	let busy = $state<'preview' | 'restore' | null>(null);
	let message = $state('');
	let fieldErrors = $state<Record<string, string>>({});
	// The overview of what a failed restore could not restore (the preview carries its own).
	let failedReport = $state<Report | null>(null);
	const report = $derived(failedReport ?? preview?.report ?? null);
	let excluded = $state<string[]>([]);
	const excludedKeys = $derived((report?.sections ?? []).filter((section) => excluded.includes(section.group)).map((section) => section.key));
	const blocked = $derived((report?.blocked ?? 0) > 0);
	// Problems with the club itself rule out any restore; otherwise the blocked items can be left out.
	const canRestore = $derived(!report?.club.length);


	let top = $state<HTMLElement>();
	// The page scrolls inside the app shell, so bring the top of the page (where the error is shown) into view.
	const showError = async () => {
		await tick();
		top?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	};

	async function run(operation: 'preview' | 'restore') {
		if (!file || busy) return;
		busy = operation;
		message = '';
		fieldErrors = {};
		failedReport = null;
		const form = new FormData();
		form.append('archive', file);
		if (operation === 'restore') {
			form.append('name', name.trim());
			form.append('slug', slug.trim());
			if (blocked) form.append('partial', 'true');
			if (excludedKeys.length) form.append('exclude', excludedKeys.join(','));
		}
		try {
			const response = await fetch(`/api/platform/v1/club-backups/${operation}`, { method: 'POST', body: form });
			const result = await response.json().catch(() => null);
			if (!response.ok) {
				if (result?.error?.report) failedReport = result.error.report as Report;
				for (const [field, errors] of Object.entries(result?.error?.fields ?? {})) {
					if (Array.isArray(errors) && errors.length) fieldErrors[field] = String(errors[0]);
				}
				if (Array.isArray(result?.detail)) {
					for (const error of result.detail) {
						const field = error.loc?.at(-1);
						if (field && error.msg) fieldErrors[field] = error.msg;
					}
				}
				message = result?.error?.message ?? (typeof result?.detail === 'string' ? result.detail : undefined)
					?? (response.status === 413 ? 'This file exceeds the server upload limit.' : Object.values(fieldErrors)[0] ?? 'The backup request failed.');
			} else if (operation === 'preview') {
				preview = result as Preview;
				excluded = [];
				name = preview.name;
				slug = preview.suggested_slug ?? preview.slug;
			} else {
				restored = result as Restored;
				preview = null;
			}
		} catch {
			message = 'Could not reach the server. Check the connection and try again.';
		} finally {
			busy = null;
			if (message) void showError();
		}
	}
</script>

<svelte:head><title>Restore club backup · RosterChief</title></svelte:head>

<div class="flex flex-wrap items-end justify-between gap-3" bind:this={top}>
	<div>
		<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Restore club backup</h1>
		<p class="mt-1.5 font-mono text-[11px] text-muted">Upload a RosterChief ZIP (up to 512 MiB) to restore it as a new club with its own slug</p>
	</div>
	<a class="btn btn-outline" href="/clubs">All clubs</a>
</div>

{#if message}<Alert kind="error" title={report ? 'The backup cannot be restored as it is' : undefined}>{message}</Alert>{/if}

{#if restored}
	<Alert kind="success" title="Club restored">
		<p>{restored.slug} is ready. {restored.new_accounts} newly restored accounts can set a password using the password reset page.</p>
	</Alert>
	{#each restored.warnings as warning (warning)}<Alert kind="warning">{warning}</Alert>{/each}
	<button class="btn btn-primary self-start" type="button" onclick={() => goto(`/clubs/${restored?.id}`)}>Open club</button>
{:else}
	<section class="flex flex-col gap-3" aria-labelledby="h-upload">
		<h2 id="h-upload" class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">Backup file</h2>
		<div class="card card-body gap-4">
			<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
				<input id="backup-zip" class="peer sr-only" type="file" accept=".zip,application/zip" disabled={!!busy} aria-label="Backup ZIP" aria-describedby="backup-zip-name" onchange={(event) => {
					file = event.currentTarget.files?.[0] ?? null;
					preview = null;
					failedReport = null;
					message = '';
					fieldErrors = {};
				}} />
				<label for="backup-zip" class="btn btn-outline gap-2 peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-disabled:pointer-events-none peer-disabled:opacity-50">
					<FileArchive size={16} aria-hidden="true" /> Choose ZIP file
				</label>
				<span id="backup-zip-name" class="min-w-0 break-all text-sm text-muted" aria-live="polite">{file?.name ?? 'No file selected'}</span>
				<div class="flex-1"></div>
				<button class="btn btn-primary gap-2" type="button" disabled={!file || !!busy} onclick={() => run('preview')}><ScanLine size={16} aria-hidden="true" /> {busy === 'preview' ? 'Checking backup…' : 'Check backup'}</button>
			</div>
		</div>
	</section>

	{#if preview}
		<section class="flex flex-col gap-3" aria-labelledby="h-contents">
			<h2 id="h-contents" class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">RosterChief v{preview.version} · {preview.name}</h2>
			<p class="-mt-1 font-mono text-[11px] text-muted">{preview.records} records · {preview.accounts} club accounts · {preview.files} files</p>
			{#each preview.warnings as warning (warning)}<Alert kind="warning">{warning}</Alert>{/each}
		</section>
	{/if}

	{#if report}
		<section class="flex flex-col gap-3" aria-labelledby="h-overview">
			<h2 id="h-overview" class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">What to restore</h2>
			<p class="-mt-1 text-sm text-muted">Everything is included. Switch off an area to leave it out, for example sponsors or evaluations.</p>
			{#if blocked}
				<Alert kind="error" title="Some of this backup cannot be restored">
					{#if report.club.length}
						The club itself cannot be restored from this backup. Fix the problems listed below in the source club and export a new backup.
					{:else}
						A full restore is not possible. Fix the blocked items in the source club and export a new backup, or restore everything else and leave the blocked items (and whatever depends on them) out.
					{/if}
				</Alert>
			{/if}
			<div class="card card-body"><RestoreReport {report} bind:excluded /></div>
		</section>
	{/if}

	{#if preview && canRestore}
		<section class="flex flex-col gap-3" aria-labelledby="h-new">
			<h2 id="h-new" class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{blocked ? 'Partial restore as a new club' : 'New club'}</h2>
			<form class="card card-body gap-4" onsubmit={(event) => { event.preventDefault(); void run('restore'); }}>
				<div class="grid gap-4 md:grid-cols-2">
					<Field label="New club name" name="name" bind:value={name} error={fieldErrors.name} required />
					<Field label="New club slug" name="slug" bind:value={slug} error={fieldErrors.slug} help={preview.suggested_slug && preview.suggested_slug !== preview.slug ? 'The original slug is unavailable here. You can use the suggested slug or enter another.' : undefined} required />
				</div>
				<button class="btn btn-primary self-start gap-2" type="submit" disabled={!!busy}><Upload size={16} aria-hidden="true" /> {busy === 'restore' ? 'Restoring club…' : blocked ? `Restore without the ${report?.blocked} blocked items` : excludedKeys.length ? 'Restore the selection as new club' : 'Restore as new club'}</button>
			</form>
		</section>
	{/if}
{/if}
