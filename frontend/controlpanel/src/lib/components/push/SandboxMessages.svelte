<script lang="ts">
	import { goto, invalidate } from '$app/navigation';
	import { ChevronRight } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { providerLabel, STATUS_BADGE, type SandboxMessages } from '#lib/push.ts';
	import { withZone } from '#lib/time.ts';

	type Query = { channel: string; provider: string; outcome: string; club: string };
	let { messages, query, clubs, error }: { messages: SandboxMessages | null; query: Query; clubs: { id: string; name: string }[]; error: string } = $props();

	let open = $state<Record<string, boolean>>({});
	let confirm = $state(false);
	let busy = $state(false);
	let failure = $state('');
	let notice = $state('');

	const channels = $derived([...new Set([...(messages?.items.map((m) => m.channel) ?? []), ...(query.channel ? [query.channel] : [])])]);
	const outcomes = $derived(Object.entries(messages?.outcomes ?? {}));

	function go(over: Partial<Query>) {
		const q = { ...query, ...over };
		const p = new URLSearchParams(window.location.search);
		for (const k of ['channel', 'provider', 'outcome', 'club'] as const) {
			if (q[k]) p.set(k, q[k]);
			else p.delete(k);
		}
		const s = p.toString();
		goto(`/push/sandbox${s ? `?${s}` : ''}`, { replaceState: true, reset: false });
	}

	async function clear() {
		busy = true;
		failure = '';
		const r = await platform<{ deleted: number }>('DELETE', '/push/sandbox/messages');
		busy = false;
		if (!r.ok) return (failure = r.message ?? 'The messages could not be cleared.');
		confirm = false;
		notice = `Cleared ${r.data?.deleted ?? 0} captured message${r.data?.deleted === 1 ? '' : 's'}.`;
		invalidate('app:push');
	}
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title">
			Captured messages <span class="badge badge-ghost ml-1">{messages?.items.length ?? 0}</span>
			<button class="btn btn-outline btn-error btn-sm ml-auto" disabled={!messages?.items.length} onclick={() => (confirm = true)}>Clear all</button>
		</div>
		<p class="-mt-1 text-xs text-muted">What the sandbox captured instead of sending. The latest 100 are shown.</p>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		{#if notice}<Alert kind="success">{notice}</Alert>{/if}
		<div class="grid grid-cols-4 gap-3">
			<select class="select select-bordered select-sm w-full min-w-0" aria-label="Channel" value={query.channel} onchange={(e) => go({ channel: e.currentTarget.value })}>
				<option value="">Channel: all</option>
				{#each channels as c (c)}<option value={c}>{c}</option>{/each}
			</select>
			<select class="select select-bordered select-sm w-full min-w-0" aria-label="Provider" value={query.provider} onchange={(e) => go({ provider: e.currentTarget.value })}>
				<option value="">Provider: all</option>
				{#each messages?.providers ?? [] as p (p)}<option value={p}>{providerLabel(p)}</option>{/each}
			</select>
			<select class="select select-bordered select-sm w-full min-w-0" aria-label="Outcome" value={query.outcome} onchange={(e) => go({ outcome: e.currentTarget.value })}>
				<option value="">Outcome: all</option>
				{#each outcomes as [o] (o)}<option value={o}>{o}</option>{/each}
			</select>
			<select class="select select-bordered select-sm w-full min-w-0" aria-label="Club" value={query.club} onchange={(e) => go({ club: e.currentTarget.value })}>
				<option value="">All clubs</option>
				{#each clubs as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
			</select>
		</div>
		{#if outcomes.length}
			<div class="flex flex-wrap gap-1.5">
				{#each outcomes as [o, n] (o)}<span class="badge {STATUS_BADGE[o] ?? 'badge-ghost'}">{o}: {n}</span>{/each}
			</div>
		{/if}
	</div>

	{#if messages?.items.length}
		<table class="table table-fixed">
			<thead><tr><th class="w-8"></th><th class="w-32">When</th><th class="w-28">Provider</th><th class="w-[18%]">To</th><th>Message</th><th class="w-24">Outcome</th></tr></thead>
			<tbody>
				{#each messages.items as m (m.id)}
					<tr class="align-top">
						<td>
							<button class="text-dim" aria-expanded={!!open[m.id]} aria-label="Show request of {m.title}" onclick={() => (open[m.id] = !open[m.id])}>
								<ChevronRight size={14} class="transition-transform {open[m.id] ? 'rotate-90' : ''}" aria-hidden="true" />
							</button>
						</td>
						<td class="font-mono text-xs break-words text-muted">{withZone(m.created, true)}</td>
						<td class="text-xs"><div>{providerLabel(m.provider)}</div><div class="text-muted">{m.channel}</div></td>
						<td class="text-xs">
							<div class="font-mono break-all">{m.user_email ?? '—'}</div>
							<div class="text-muted">{m.target_label}</div>
							{#if m.club}<div class="text-muted">{m.club.name}</div>{/if}
						</td>
						<td class="text-xs"><div class="font-semibold break-words text-ink">{m.title}</div><div class="break-words text-slate">{m.body}</div></td>
						<td><span class="badge {STATUS_BADGE[m.outcome] ?? 'badge-ghost'}">{m.outcome}</span></td>
					</tr>
					{#if open[m.id]}
						<tr class="bg-subhead">
							<td></td>
							<td colspan="5"><pre class="max-h-64 overflow-auto rounded border border-edge bg-white p-3 font-mono text-[11px] whitespace-pre-wrap text-ink select-text">{JSON.stringify(m.request, null, 2)}</pre></td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>
	{:else}
		<div class="card-body pt-0"><p class="text-sm text-muted">{query.channel || query.provider || query.outcome || query.club ? 'No captured messages match these filters.' : 'Nothing captured yet.'}</p></div>
	{/if}
</div>

<Modal bind:open={confirm} title="Clear captured messages">
	<div class="flex flex-col gap-4">
		<p class="text-sm">This permanently deletes every captured message, whatever the filters. The audit trail is kept.</p>
		{#if failure}<Alert kind="error">{failure}</Alert>{/if}
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (confirm = false)}>Cancel</button>
			<button type="button" class="btn btn-error" disabled={busy} onclick={clear}>Clear all</button>
		</div>
	</div>
</Modal>
