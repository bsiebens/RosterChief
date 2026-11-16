<script lang="ts">
	import { onMount } from 'svelte';
	import { CalendarClock, CircleCheck, CircleX, LoaderCircle, RefreshCw } from '@lucide/svelte';
	import type { Job, JobDetail } from '#lib/dashboard.ts';
	import Modal from './Modal.svelte';
	import { withZone, zoneLabel } from '#lib/time.ts';

	const FILTERS = [
		['', 'All'],
		['failure', 'Failed'],
		['running', 'Running'],
		['success', 'Succeeded']
	] as const;

	let jobs = $state<Job[] | null>(null);
	let filter = $state<string>('');
	let error = $state(false);
	let loading = $state(false);

	async function load() {
		loading = true;
		try {
			const res = await fetch(`/api/platform/v1/jobs?limit=15${filter ? `&status=${filter}` : ''}`);
			if (res.ok) {
				jobs = await res.json();
				error = false;
			} else error = true;
		} catch {
			error = true;
		} finally {
			loading = false;
		}
	}

	let detailOpen = $state(false);
	let detail = $state<JobDetail | null>(null);
	let detailError = $state('');
	let copied = $state(false);

	async function open(job: Job) {
		detail = { ...job, traceback: null, result: null, args: null, kwargs: null, periodic_task_name: null };
		detailError = '';
		detailOpen = true;
		try {
			const res = await fetch(`/api/platform/v1/jobs/${encodeURIComponent(job.id)}`);
			if (res.ok) detail = await res.json();
			else detailError = 'The full details could not be loaded.';
		} catch {
			detailError = 'The full details could not be loaded.';
		}
	}

	async function copyTrace() {
		try {
			await navigator.clipboard.writeText(detail?.traceback || detail?.result || '');
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			// clipboard unavailable: the text is selectable on screen
		}
	}

	onMount(() => {
		const timer = setInterval(() => !document.hidden && load(), 15_000);
		return () => clearInterval(timer);
	});

	$effect(() => {
		filter; // reload whenever the filter changes (and once on mount)
		load();
	});

	const time = (iso: string | null) => withZone(iso, true);
	let zoneShort = $state('');
	onMount(() => (zoneShort = zoneLabel()));
	const duration = (s: number | null) => (s === null ? '' : s < 1 ? '<1s' : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);
	// "controlpanel.provision_club" -> "provision_club"; the prefix is the app and adds only noise.
	const short = (name: string) => name.split('.').pop() ?? name;
</script>

<section class="card flex flex-col" id="jobs" aria-labelledby="jobs-title">
	<div class="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
		<h2 id="jobs-title" class="font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase">Job log</h2>
		<span class="font-mono text-[11px] text-muted">celery · refreshes every 15s · times in your timezone</span>
		<span class="flex-1"></span>
		<div class="flex gap-1" role="group" aria-label="Filter jobs">
			{#each FILTERS as [value, label] (value)}
				<button class="btn btn-xs {filter === value ? '' : 'btn-outline'}" aria-pressed={filter === value} onclick={() => (filter = value)}>{label}</button>
			{/each}
		</div>
		<button class="btn btn-outline btn-xs" onclick={load} disabled={loading} aria-label="Refresh job log"><RefreshCw size={12} class={loading ? 'animate-spin' : ''} aria-hidden="true" /></button>
	</div>

	<div class="overflow-x-auto">
		<table class="table">
			<thead>
				<tr><th>Started{zoneShort ? ` (${zoneShort})` : ''}</th><th>Job</th><th>Status</th><th class="text-right">Took</th><th>Detail</th></tr>
			</thead>
			<tbody>
				{#if jobs === null && !error}
					<tr><td colspan="5" class="text-center text-muted">Loading…</td></tr>
				{:else if error && jobs === null}
					<tr><td colspan="5" class="text-center text-club-dark">The job log could not be loaded.</td></tr>
				{:else}
					{#each jobs ?? [] as job (job.id)}
						<tr class="cursor-pointer hover:bg-row-focus {job.status === 'failure' ? 'bg-row-sel' : ''}" onclick={() => open(job)}>
							<td class="font-mono text-xs whitespace-nowrap text-muted">{time(job.started_at)}</td>
							<td>
								<div class="flex items-center gap-1.5 font-mono text-xs text-ink">
									<button class="text-left hover:underline" onclick={(e) => (e.stopPropagation(), open(job))} aria-label="Show details of {short(job.name)}">{short(job.name)}</button>
									{#if job.scheduled}<CalendarClock size={12} class="text-dim" aria-label="Scheduled by beat" />{/if}
								</div>
							</td>
							<td>
								{#if job.status === 'success'}
									<span class="badge badge-success"><CircleCheck size={11} aria-hidden="true" /> ok</span>
								{:else if job.status === 'failure'}
									<span class="badge badge-error"><CircleX size={11} aria-hidden="true" /> failed</span>
								{:else if job.status === 'running' || job.status === 'pending'}
									<span class="badge badge-info"><LoaderCircle size={11} class="animate-spin" aria-hidden="true" /> {job.status}</span>
								{:else}
									<span class="badge badge-ghost">{job.status}</span>
								{/if}
							</td>
							<td class="text-right font-mono text-xs tabular-nums text-muted">{duration(job.duration_seconds)}</td>
							<td class="max-w-[28rem] truncate font-mono text-xs {job.status === 'failure' ? 'text-club-dark' : 'text-muted'}" title={job.detail}>{job.detail}</td>
						</tr>
					{:else}
						<tr><td colspan="5" class="text-center text-muted">{filter ? 'No jobs with this status.' : 'No job runs recorded yet.'}</td></tr>
					{/each}
				{/if}
			</tbody>
		</table>
	</div>
</section>

<Modal bind:open={detailOpen} title={detail ? short(detail.name) : 'Job'} wide>
	{#if detail}
		<dl class="mb-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 font-mono text-xs">
			<dt class="text-muted">status</dt>
			<dd class={detail.status === 'failure' ? 'font-bold text-club-dark' : 'text-ink'}>{detail.status}</dd>
			<dt class="text-muted">task</dt>
			<dd class="break-all text-ink">{detail.name}</dd>
			<dt class="text-muted">started</dt>
			<dd class="text-ink">{time(detail.started_at)}</dd>
			<dt class="text-muted">finished</dt>
			<dd class="text-ink">{time(detail.finished_at)}{detail.duration_seconds !== null ? ` · took ${duration(detail.duration_seconds)}` : ''}</dd>
			<dt class="text-muted">trigger</dt>
			<dd class="text-ink">{detail.periodic_task_name ? `beat · ${detail.periodic_task_name}` : 'queued by the application'}</dd>
			{#if detail.worker}<dt class="text-muted">worker</dt><dd class="break-all text-ink">{detail.worker}</dd>{/if}
			{#if detail.args && detail.args !== '[]' && detail.args !== '()'}<dt class="text-muted">args</dt><dd class="break-all text-ink">{detail.args}</dd>{/if}
			{#if detail.kwargs && detail.kwargs !== '{}'}<dt class="text-muted">kwargs</dt><dd class="break-all text-ink">{detail.kwargs}</dd>{/if}
		</dl>

		{#if detailError}<p class="mb-3 text-sm text-club-dark">{detailError}</p>{/if}

		{#if detail.status === 'failure'}
			<div class="mb-1.5 flex items-center justify-between">
				<h3 class="font-display text-xs font-extrabold tracking-[.1em] text-club-dark uppercase">Error</h3>
				<button class="btn btn-outline btn-xs" onclick={copyTrace}>{copied ? 'Copied' : 'Copy'}</button>
			</div>
			<pre class="max-h-[50vh] overflow-auto rounded border border-danger-border bg-danger-bg p-3 font-mono text-[11px] leading-relaxed whitespace-pre text-club-dark select-text">{detail.traceback || detail.result || detail.detail || 'No error details were recorded.'}</pre>
		{:else if detail.result}
			<h3 class="mb-1.5 font-display text-xs font-extrabold tracking-[.1em] text-ink uppercase">Result</h3>
			<pre class="max-h-[40vh] overflow-auto rounded border border-edge bg-subhead p-3 font-mono text-[11px] whitespace-pre-wrap text-ink select-text">{detail.result}</pre>
		{/if}
	{/if}
	<div class="modal-action"><button class="btn btn-primary btn-sm" onclick={() => (detailOpen = false)}>Close</button></div>
</Modal>
