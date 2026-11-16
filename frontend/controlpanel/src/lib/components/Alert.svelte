<script lang="ts">
	import { CircleAlert, CircleCheck, Info, TriangleAlert } from '@lucide/svelte';
	import type { Snippet } from 'svelte';

	let {
		kind = 'error',
		title,
		children
	}: { kind?: 'error' | 'warning' | 'success' | 'info'; title?: string; children: Snippet } = $props();

	const icon = $derived({ error: CircleAlert, warning: TriangleAlert, success: CircleCheck, info: Info }[kind]);
	const Ico = $derived(icon);
</script>

<div class="alert alert-{kind}" role={kind === 'error' ? 'alert' : 'status'}>
	<Ico size={18} class="mt-0.5 shrink-0" aria-hidden="true" />
	<div>
		{#if title}<div class="font-display text-sm font-bold tracking-wide uppercase">{title}</div>{/if}
		<div class="text-sm">{@render children()}</div>
	</div>
</div>
