<script lang="ts">
	import type { Snippet } from 'svelte';
	let { title, lead, children, footer }: { title: string; lead?: string; children: Snippet; footer?: Snippet } = $props();
</script>

<svelte:head><title>{title} · RosterChief</title></svelte:head>

<div class="card">
	<div class="card-body gap-0 p-7">
		<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">{title}</h1>
		{#if lead}<p class="mt-2 text-sm text-muted">{lead}</p>{/if}
		<div class="mt-6 flex flex-col gap-4">{@render children()}</div>
	</div>
</div>
{#if footer}
	<div class="text-center font-mono text-xs text-on-dark-dim [&_a]:text-white [&_a]:underline [&_a:hover]:no-underline">{@render footer()}</div>
{/if}
