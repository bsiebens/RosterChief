<script lang="ts">
	import type { SandboxOverview } from '#lib/push.ts';
	import { withZone } from '#lib/time.ts';

	let { events }: { events: SandboxOverview['events'] } = $props();

	const fmt = (d: unknown) => (d === null || d === undefined || d === '' ? '—' : typeof d === 'string' ? d : JSON.stringify(d));
</script>

<div class="card">
	<div class="card-body pb-2">
		<div class="card-title">Audit trail</div>
		<p class="-mt-1 text-xs text-muted">The latest 20 changes to the sandbox.</p>
	</div>
	{#if events.length}
		<table class="table table-fixed">
			<thead><tr><th class="w-36">When</th><th class="w-[28%]">Who</th><th class="w-[20%]">Action</th><th>Detail</th></tr></thead>
			<tbody>
				{#each events as e (e.id)}
					<tr class="align-top">
						<td class="font-mono text-xs break-words text-muted">{withZone(e.created, true)}</td>
						<td class="font-mono text-xs break-all">{e.user_email ?? 'system'}</td>
						<td class="text-xs font-semibold break-words text-ink">{e.action}</td>
						<td class="font-mono text-xs break-words">{fmt(e.detail)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<div class="card-body pt-0"><p class="text-sm text-muted">Nothing recorded yet.</p></div>
	{/if}
</div>
