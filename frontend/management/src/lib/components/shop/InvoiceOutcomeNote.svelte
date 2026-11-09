<script lang="ts">
	import { CircleAlert, CircleCheck } from '@lucide/svelte';
	import type { InvoiceOutcome } from '#lib/shop-orders.ts';

	/** The result of an invoice action as a small chip on its own line (success green, error red). */
	let { outcome }: { outcome: InvoiceOutcome } = $props();
</script>

<span
	class="inline-flex items-center gap-1.5 self-start rounded-lg border px-2.5 py-1 text-xs font-semibold {outcome.kind === 'done' ? 'border-ok-border bg-ok-bg text-ok-text' : 'border-danger-border bg-danger-bg text-danger'}"
	role={outcome.kind === 'failed' ? 'alert' : 'status'}>
	{#if outcome.kind === 'done'}<CircleCheck size={14} aria-hidden="true" />{:else}<CircleAlert size={14} aria-hidden="true" />{/if}
	{outcome.message}
</span>
