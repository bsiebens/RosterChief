<script lang="ts">
	import type { PushMonitoring } from '#lib/push.ts';

	let { series, window }: { series: PushMonitoring['series']; window: string } = $props();

	const max = $derived(Math.max(1, ...series.map((b) => b.sent + b.not_sent)));
	const label = (iso: string) => {
		const d = new Date(iso);
		return window === '7d' ? d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
	};
	const total = $derived(series.reduce((a, b) => a + b.sent + b.not_sent, 0));
</script>

<div class="card">
	<div class="card-body gap-3">
		<div class="card-title">
			Sent vs not sent
			<span class="ml-auto flex items-center gap-3 font-mono text-[11px] font-normal tracking-normal text-muted normal-case">
				<span class="flex items-center gap-1"><span class="inline-block h-2.5 w-2.5 rounded-sm bg-ok"></span> sent</span>
				<span class="flex items-center gap-1"><span class="inline-block h-2.5 w-2.5 rounded-sm bg-club"></span> not sent</span>
			</span>
		</div>
		{#if !total}
			<p class="text-sm text-muted">No deliveries in this window.</p>
		{:else}
			<div class="flex h-40 items-end gap-1" role="img" aria-label="Sent and not sent deliveries per time bucket">
				{#each series as b (b.bucket)}
					<div class="flex h-full min-w-0 flex-1 flex-col justify-end" title="{label(b.bucket)}: {b.sent} sent, {b.not_sent} not sent">
						<div class="w-full bg-club" style="height: {(b.not_sent / max) * 100}%"></div>
						<div class="w-full bg-ok" style="height: {(b.sent / max) * 100}%"></div>
					</div>
				{/each}
			</div>
			<div class="flex gap-1 font-mono text-[10px] text-muted">
				{#each series as b, i (b.bucket)}
					<div class="min-w-0 flex-1 overflow-hidden text-center text-ellipsis whitespace-nowrap">{series.length > 12 && i % 2 ? '' : label(b.bucket)}</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
