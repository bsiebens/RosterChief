<script lang="ts">
	import { withZone } from '#lib/time.ts';
	import { ms, pct, STATE_BADGE, type ProviderHealth } from '#lib/push.ts';

	let { p }: { p: ProviderHealth } = $props();
</script>

<div class="card">
	<div class="card-body gap-2 text-sm">
		<div class="card-title flex-wrap gap-y-1">
			{p.label}
			{#if p.enabled === false}
				<span class="badge badge-ghost badge-sm">disabled</span>
			{:else if p.configured === true}
				<span class="badge badge-success badge-sm">configured</span>
			{:else if p.configured === false}
				<span class="badge badge-warning badge-sm">not configured</span>
			{/if}
			<span class="badge {STATE_BADGE[p.state]} ml-auto">{p.state}</span>
		</div>
		<div class="flex items-end gap-2">
			<span class="font-display text-4xl leading-none font-extrabold text-ink tabular-nums">{pct(p.success_rate)}</span>
			<span class="pb-0.5 font-mono text-[11px] text-muted">success rate · {p.requests} requests</span>
		</div>
		<dl class="grid grid-cols-4 gap-2 text-center">
			{#each [['sent', p.sent], ['failed', p.failed], ['dead', p.dead], ['retrying', p.retrying]] as [label, n] (label)}
				<div class="rounded border border-line px-1 py-1.5">
					<dd class="font-mono text-sm font-semibold text-ink tabular-nums">{n}</dd>
					<dt class="text-[11px] text-muted">{label}</dt>
				</div>
			{/each}
		</dl>
		<div class="font-mono text-xs text-muted">p50 <span class="text-ink">{ms(p.p50_ms)}</span> · p95 <span class="text-ink">{ms(p.p95_ms)}</span></div>
		<div class="font-mono text-[11px] text-muted">
			<div>Last success: {withZone(p.last_success)}</div>
			<div>Last failure: {withZone(p.last_failure)}</div>
		</div>
	</div>
</div>
