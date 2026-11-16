<script lang="ts">
	import { goto } from '$app/navigation';
	import { navigating } from '$app/state';
	import Alert from '#lib/components/Alert.svelte';
	import Kpi from '#lib/components/Kpi.svelte';
	import ProviderCard from '#lib/components/push/ProviderCard.svelte';
	import SentSeries from '#lib/components/push/SentSeries.svelte';
	import { providerLabel, STATUS_BADGE } from '#lib/push.ts';
	import { withZone } from '#lib/time.ts';

	let { data } = $props();

	const m = $derived(data.monitoring);
	const loading = $derived(!!navigating.to);

	function go(over: Record<string, string>) {
		const q = { ...data.query, ...over };
		const p = new URLSearchParams();
		if (q.window !== '24h') p.set('window', q.window);
		if (q.club) p.set('club', q.club);
		const s = p.toString();
		goto(`/push${s ? `?${s}` : ''}`, { replaceState: true, reset: false });
	}

	const WINDOWS = [
		['1h', 'Last hour'],
		['24h', 'Last 24 hours'],
		['7d', 'Last 7 days']
	];

	// A platform with devices but no working provider: those devices can never get a push.
	const mismatches = $derived.by(() => {
		if (!m) return [];
		const out: string[] = [];
		const check = (provider: string, count: number, name: string) => {
			const p = m.providers.find((x) => x.provider === provider);
			if (p && count > 0 && (p.configured === false || p.enabled === false)) out.push(`${count} enabled ${name} device${count === 1 ? '' : 's'} but ${providerLabel(provider)} is ${p.configured === false ? 'not configured' : 'disabled'}.`);
		};
		check('apns', m.devices.ios.enabled, 'iOS');
		check('fcm', m.devices.android.enabled, 'Android');
		check('webpush', m.devices.web.subscriptions, 'web push');
		return out;
	});
</script>

<svelte:head><title>Push monitoring · RosterChief</title></svelte:head>

<div class="flex flex-wrap items-center gap-x-6 gap-y-3 py-1">
	<div class="flex flex-wrap items-center gap-2" role="group" aria-label="Time window">
		{#each WINDOWS as [value, label] (value)}
			<button class="btn btn-sm {data.query.window === value ? 'btn-neutral' : 'btn-outline'}" aria-pressed={data.query.window === value} onclick={() => go({ window: value })}>{label}</button>
		{/each}
	</div>
	<select class="select select-bordered select-sm w-full sm:w-64" aria-label="Club" value={data.query.club} onchange={(e) => go({ club: e.currentTarget.value })}>
		<option value="">All clubs</option>
		{#each data.clubs as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
	</select>
	{#if loading}<span class="font-mono text-[11px] text-muted">Loading…</span>{/if}
</div>

{#if data.error || !m}
	<Alert kind="error">{data.error || 'The push monitoring could not be loaded.'}</Alert>
{:else}
	{#if m.sandbox.enabled}
		<Alert kind="warning" title="Sandbox is on">Nothing is sent to users right now{m.sandbox.expires_at ? `; it switches back to live at ${withZone(m.sandbox.expires_at)}` : ''}. <a class="link font-semibold" href="/push/sandbox">Open the sandbox</a></Alert>
	{/if}
	{#each m.alerts as a (a.id)}
		<Alert kind="error" title="Open alert">{a.message} <span class="font-mono text-xs">Since {withZone(a.first_seen)} · {a.notifications_sent} notification{a.notifications_sent === 1 ? '' : 's'} sent</span></Alert>
	{/each}
	{#each mismatches as text (text)}
		<Alert kind="warning" title="Provider mismatch">{text} <a class="link font-semibold" href="/push/config">Open the configuration</a></Alert>
	{/each}

	<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
		{#each m.providers as p (p.provider)}<ProviderCard {p} />{/each}
	</div>
	{#if !m.providers.length}<p class="text-sm text-muted">No provider data in this window.</p>{/if}

	<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
		<Kpi label="retry queue" value={m.retry_queue.waiting} sub={m.retry_queue.oldest_next_attempt ? `next attempt ${withZone(m.retry_queue.oldest_next_attempt)}` : 'nothing waiting'} tone={m.retry_queue.stuck ? 'club' : 'none'} />
		<Kpi label="overdue retries" value={m.retry_queue.overdue} sub="past their next attempt" tone={m.retry_queue.overdue ? 'warn' : 'none'} />
		<Kpi label="outbox pending" value={m.outbox.pending} sub={m.outbox.oldest_pending ? `oldest ${withZone(m.outbox.oldest_pending)}` : 'empty'} tone={m.outbox.pending ? 'warn' : 'none'} />
		<Kpi label="outbox failed" value={m.outbox.failed} sub="entries that gave up" tone={m.outbox.failed ? 'club' : 'none'} />
	</div>
	{#if m.retry_queue.stuck}
		<Alert kind="error" title="Retry queue is stuck">
			Retries are overdue and nothing is processing them. Check that the Celery worker and beat are running.
			{#if m.retry_queue.reasons.length}
				<ul class="mt-1 list-disc pl-4">{#each m.retry_queue.reasons as r (r)}<li>{r}</li>{/each}</ul>
			{/if}
		</Alert>
	{/if}

	<SentSeries series={m.series} window={m.window} />

	<div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
		<div class="card">
			<div class="card-body pb-2"><div class="card-title">Top failure reasons</div></div>
			{#if m.top_failures.length}
				<table class="table">
					<thead><tr><th>Provider</th><th>Reason</th><th class="text-right">Count</th><th>Last seen</th></tr></thead>
					<tbody>
						{#each m.top_failures as f (f.provider + f.error)}
							<tr>
								<td class="whitespace-nowrap">{providerLabel(f.provider)}</td>
								<td class="font-mono text-xs break-words">{f.error || '—'}</td>
								<td class="text-right font-mono tabular-nums">{f.count}</td>
								<td class="font-mono text-xs whitespace-nowrap text-muted">{withZone(f.last_seen)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<div class="card-body pt-0"><p class="text-sm text-muted">No failures in this window.</p></div>
			{/if}
		</div>

		<div class="card">
			<div class="card-body gap-2 text-sm">
				<div class="card-title">Registered devices</div>
				<table class="table">
					<thead><tr><th>Platform</th><th class="text-right">Enabled</th><th class="text-right">Disabled</th><th class="text-right">Stale</th></tr></thead>
					<tbody>
						<tr><td>iOS</td><td class="text-right font-mono">{m.devices.ios.enabled}</td><td class="text-right font-mono">{m.devices.ios.disabled}</td><td class="text-right font-mono">{m.devices.ios.stale}</td></tr>
						<tr><td>Android</td><td class="text-right font-mono">{m.devices.android.enabled}</td><td class="text-right font-mono">{m.devices.android.disabled}</td><td class="text-right font-mono">{m.devices.android.stale}</td></tr>
						<tr><td>Web subscriptions</td><td class="text-right font-mono">{m.devices.web.subscriptions}</td><td class="text-right text-dim">—</td><td class="text-right text-dim">—</td></tr>
					</tbody>
				</table>
				<p class="text-xs text-muted">Stale devices have not been seen for a while and are cleaned up automatically.</p>
			</div>
		</div>
	</div>

	<div class="card">
		<div class="card-body pb-2">
			<div class="card-title">Recent problems <span class="badge badge-ghost ml-1">{m.recent_problems.length}</span></div>
			<p class="-mt-1 text-xs text-muted">The latest 50 deliveries that were not sent.</p>
		</div>
		{#if m.recent_problems.length}
			<table class="table table-fixed">
				<thead><tr><th class="w-32">When</th><th class="w-28">Provider</th><th class="w-[16%]">Club</th><th class="w-[16%]">User</th><th class="w-[14%]">Target</th><th class="w-24">Status</th><th class="w-14">Tries</th><th>Error</th></tr></thead>
				<tbody>
					{#each m.recent_problems as r (r.id)}
						<tr class="align-top">
							<td class="font-mono text-xs break-words text-muted">{withZone(r.created, true)}</td>
							<td>{providerLabel(r.provider)}</td>
							<td class="text-xs break-words">{r.club?.name ?? '—'}</td>
							<td class="font-mono text-xs break-all">
								{#if r.user_id}<a class="link" href="/users/{r.user_id}">{r.user_email_masked ?? 'open'}</a>{:else}{r.user_email_masked ?? '—'}{/if}
							</td>
							<td class="font-mono text-xs break-all">{r.target_masked}</td>
							<td><span class="badge {STATUS_BADGE[r.status] ?? 'badge-ghost'}">{r.status}</span></td>
							<td class="font-mono tabular-nums">{r.attempts}</td>
							<td class="font-mono text-xs break-words">{r.error || '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			<div class="card-body pt-0"><p class="text-sm text-muted">No problems in this window.</p></div>
		{/if}
	</div>
{/if}
