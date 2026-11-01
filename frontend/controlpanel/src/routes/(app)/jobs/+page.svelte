<script lang="ts">
	import { onMount } from 'svelte';
	import { CalendarCog, ChevronRight, Clock, Pause, Play, RefreshCw, RotateCcw } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import { describe, raw } from '#lib/cron.ts';
	import { browserZone, scheduleZone, withZone, zoneLabel } from '#lib/time.ts';
	import type { Cron, PeriodicJob } from '#lib/dashboard.ts';
	import Alert from '#lib/components/Alert.svelte';
	import ScheduleEditor from '#lib/components/ScheduleEditor.svelte';

	let { data } = $props();

	let live = $state<PeriodicJob[] | null>(null);
	const jobs = $derived(live ?? data.jobs);
	const canEdit = $derived(data.auth.status === 'authenticated' && data.auth.me.level === 'superuser');

	let expanded = $state<Record<string, boolean>>({});
	let editing = $state<PeriodicJob | null>(null);
	let notice = $state<{ kind: 'success' | 'error'; text: string } | null>(null);
	let busy = $state<string | null>(null);

	const summary = $derived({
		scheduled: jobs.filter((j) => j.enabled).length,
		failing: jobs.filter((j) => j.state === 'error').length,
		paused: jobs.filter((j) => !j.enabled).length
	});

	async function reload() {
		const r = await platform<PeriodicJob[]>('GET', '/jobs/scheduled');
		if (r.ok) live = r.data!;
	}

	onMount(() => {
		const timer = setInterval(() => !document.hidden && reload(), 15_000);
		return () => clearInterval(timer);
	});

	function replace(updated: PeriodicJob) {
		live = jobs.map((j) => (j.name === updated.name ? updated : j));
	}

	async function act(job: PeriodicJob, key: string, call: () => ReturnType<typeof platform>, success: string) {
		busy = `${job.name}:${key}`;
		const r = await call();
		busy = null;
		if (!r.ok) return (notice = { kind: 'error', text: r.message ?? 'That did not work.' });
		notice = { kind: 'success', text: success };
		const updated = r.data as Partial<PeriodicJob> | undefined;
		if (updated?.name) replace(updated as PeriodicJob);
		return r;
	}

	const toggle = (job: PeriodicJob) =>
		act(job, 'toggle', () => platform('PUT', `/jobs/scheduled/${encodeURIComponent(job.name)}/enabled`, { enabled: !job.enabled }), job.enabled ? `“${job.label}” is paused.` : `“${job.label}” will run on its schedule again.`);

	async function runNow(job: PeriodicJob) {
		const r = await act(job, 'run', () => platform('POST', `/jobs/scheduled/${encodeURIComponent(job.name)}/run`), `“${job.label}” is queued. It shows in the job log once a worker picks it up.`);
		if (r) setTimeout(reload, 3000);
	}

	const reset = (job: PeriodicJob) => act(job, 'reset', () => platform('POST', `/jobs/scheduled/${encodeURIComponent(job.name)}/reset`), `“${job.label}” is back on its default schedule.`);

	async function saveSchedule(name: string, cron: Cron): Promise<string | null> {
		const r = await platform<PeriodicJob>('PUT', `/jobs/scheduled/${encodeURIComponent(name)}/schedule`, cron);
		if (!r.ok) return r.message ?? 'The schedule could not be saved.';
		replace(r.data!);
		notice = { kind: 'success', text: `Schedule saved. The scheduler picks it up within a few seconds.` };
		return null;
	}

	const when = (iso: string | null) => withZone(iso);
	let zone = $state('');
	let zoneShort = $state('');
	onMount(() => ((zone = browserZone()), (zoneShort = zoneLabel())));
	const took = (s: number | null | undefined) => (s == null ? '' : s < 1 ? '<1s' : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);
	function relative(iso: string | null) {
		if (!iso) return '';
		const s = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
		const a = Math.abs(s);
		const t = a < 90 ? `${a}s` : a < 5400 ? `${Math.round(a / 60)} min` : a < 129600 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
		return s >= 0 ? `in ${t}` : `${t} ago`;
	}
	const badge = { ok: 'badge-success', error: 'badge-error', running: 'badge-info', paused: 'badge-warning', never: '' } as const;
	const barColor = (s: string) => (s === 'success' ? 'bg-ok' : s === 'failure' ? 'bg-club' : 'bg-ice');
	const columns = 'grid-cols-[1.5rem_6rem_minmax(14rem,1fr)_14rem_10rem_8.5rem_17.5rem]';
</script>

<svelte:head><title>Jobs · RosterChief</title></svelte:head>

<div class="flex flex-wrap items-end justify-between gap-3">
	<div>
		<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Jobs</h1>
		<p class="mt-1.5 font-mono text-[11px] text-muted">
			{summary.scheduled} scheduled · <span class={summary.failing ? 'text-club-dark' : ''}>{summary.failing} failing</span> · <span class={summary.paused ? 'text-warn-text' : ''}>{summary.paused} paused</span>
		</p>
	</div>
	<button class="btn btn-outline btn-sm" onclick={reload}><RefreshCw size={14} aria-hidden="true" /> Refresh</button>
</div>

<Alert kind="info">
	Jobs run on celery beat; their schedules are stored in the database, so a change here survives restarts and applies without redeploying. “Run now” queues one run for a worker. A paused job skips its schedule but can still be run by hand.
	{#if !canEdit}Only superusers can change jobs.{/if}
</Alert>
<p class="-mt-2 font-mono text-[11px] text-muted">
	Schedules run in the zone shown next to them (the server's, usually UTC). Dates in this table are converted to your browser's timezone{zone ? `: ${zone} (${zoneShort})` : ''}, so a 04:00 UTC job can show a different clock time here.
</p>

{#if data.error}<Alert kind="error">{data.error}</Alert>{/if}
{#if notice}<Alert kind={notice.kind}>{notice.text}</Alert>{/if}

<div class="card overflow-x-auto">
	<div class="min-w-[64rem]">
		<div class="grid {columns} items-center gap-3 border-b border-line bg-subhead px-4 py-2 font-mono text-[10px] tracking-[.06em] text-dim uppercase">
			<span></span><span>Status</span><span>Job</span><span>Schedule</span><span>Next run{zoneShort ? ` (${zoneShort})` : ''}</span><span>Recent</span><span class="text-right">Actions</span>
		</div>

		{#each jobs as job (job.name)}
			{@const open = expanded[job.name] ?? job.state === 'error'}
			<div class="border-b border-rule last:border-b-0">
				<div class="grid {columns} min-h-12 items-center gap-3 px-4 py-1.5 {job.state === 'error' ? 'bg-row-warn' : ''}">
					<button class="text-dim" onclick={() => (expanded[job.name] = !open)} aria-expanded={open} aria-label="{open ? 'Hide' : 'Show'} details of {job.label}">
						<ChevronRight size={14} class="transition-transform {open ? 'rotate-90' : ''}" aria-hidden="true" />
					</button>
					<span><span class="badge badge-sm {badge[job.state]}">{job.state === 'never' ? 'never run' : job.state}</span></span>
					<span class="min-w-0">
						<span class="block truncate font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase {job.enabled ? '' : 'opacity-60'}" title={job.label}>{job.label}</span>
						<span class="block truncate font-mono text-[11px] text-muted">{job.name}</span>
					</span>
					<span class="min-w-0">
						{#if job.schedule}
							<span class="flex items-center gap-1.5 truncate text-xs text-ink"><Clock size={12} class="shrink-0 text-dim" aria-hidden="true" />{describe(job.schedule)}{#if job.schedule.timezone}<span class="text-muted"> {scheduleZone(job.schedule.timezone)}</span>{/if}</span>
							<span class="flex items-center gap-1.5 font-mono text-[11px] text-muted">{raw(job.schedule)}{#if job.edited}<span class="badge badge-warning badge-xs">edited</span>{/if}</span>
						{:else}
							<span class="text-xs text-muted">no cron schedule</span>
						{/if}
					</span>
					<span class="font-mono text-[11px] text-slate">
						{#if job.enabled && job.next_run_at}{when(job.next_run_at)}<span class="block text-dim">{relative(job.next_run_at)}</span>{:else}<span class="text-dim">—</span>{/if}
					</span>
					<span class="flex items-center gap-0.5">
						{#each job.recent as status, i (i)}<span class="h-4 w-1.5 rounded-sm {barColor(status)}" title={status}></span>{:else}<span class="font-mono text-[11px] text-dim">—</span>{/each}
					</span>
					<span class="flex items-center justify-end gap-2">
						{#if canEdit}
							<button class="btn btn-outline btn-sm {job.enabled ? 'btn-error' : 'btn-success'}" onclick={() => toggle(job)} disabled={busy === `${job.name}:toggle`}>
								{#if job.enabled}<Pause size={13} aria-hidden="true" /> Pause{:else}<Play size={13} aria-hidden="true" /> Resume{/if}
							</button>
							<button class="btn btn-outline btn-sm" onclick={() => runNow(job)} disabled={busy === `${job.name}:run`}><RefreshCw size={13} aria-hidden="true" /> Run now</button>
							<button class="btn btn-outline btn-sm btn-square" onclick={() => (editing = job)} disabled={!job.schedule} aria-label="Change schedule of {job.label}" title="Change schedule"><CalendarCog size={13} aria-hidden="true" /></button>
						{/if}
					</span>
				</div>

				{#if open}
					<div class="grid gap-4 border-t border-rule bg-subhead px-4 py-3 text-sm lg:grid-cols-[1fr_18rem]">
						<div class="flex min-w-0 flex-col gap-2">
							<p class="text-slate">{job.description || 'No description.'}</p>
							<div class="font-mono text-[11px] text-muted">{job.task}{job.schedule?.timezone ? ` · ${job.schedule.timezone}` : ''} · {job.total_run_count} scheduled run{job.total_run_count === 1 ? '' : 's'}</div>
							{#if job.latest}
								<div class="font-mono text-[11px] text-muted">last run {when(job.latest.started_at)}{job.latest.duration_seconds != null ? ` · took ${took(job.latest.duration_seconds)}` : ''} · {job.latest.status}</div>
								{#if job.latest.detail}
									<div class="rounded border px-2.5 py-2 font-mono text-xs break-words whitespace-pre-wrap {job.latest.status === 'failure' ? 'border-danger-border bg-danger-bg text-club-dark' : 'border-line bg-white text-slate'}">{job.latest.detail}</div>
								{/if}
							{:else}
								<div class="font-mono text-[11px] text-dim">No runs recorded yet.</div>
							{/if}
						</div>
						<div class="flex flex-col items-start gap-2">
							{#if job.default_schedule}
								<div class="font-mono text-[10px] tracking-[.06em] text-dim uppercase">Default schedule</div>
								<div class="text-xs text-ink">{describe(job.default_schedule)}{job.schedule?.timezone ? ` ${scheduleZone(job.schedule.timezone)}` : ''} <span class="font-mono text-muted">· {raw(job.default_schedule)}</span></div>
								{#if job.edited && canEdit}
									<button class="btn btn-outline btn-xs" onclick={() => reset(job)} disabled={busy === `${job.name}:reset`}><RotateCcw size={12} aria-hidden="true" /> Reset to default</button>
								{/if}
							{:else}
								<div class="text-xs text-muted">{job.builtin ? 'Built into celery.' : 'Created outside the code defaults, so there is nothing to reset to.'}</div>
							{/if}
						</div>
					</div>
				{/if}
			</div>
		{:else}
			<div class="px-4 py-8 text-center text-sm text-muted">No periodic jobs yet. They are created when celery beat starts.</div>
		{/each}
	</div>
</div>

<ScheduleEditor bind:job={editing} onsave={saveSchedule} />
