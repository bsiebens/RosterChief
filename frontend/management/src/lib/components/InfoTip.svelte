<script lang="ts">
	import { Info } from '@lucide/svelte';

	/** Info icon that shows a short note on hover, keyboard focus or tap (same behaviour as the members list note). */
	let { label, text, align = 'left' }: { label: string; text: string; align?: 'left' | 'right' } = $props();
	let open = $state(false);
	let hover = $state(false);
	const shown = $derived(open || hover);
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (open = false)} onclick={() => (open = false)} />

<span class="relative inline-flex" role="presentation" onmouseenter={() => (hover = true)} onmouseleave={() => (hover = false)}>
	<button
		type="button"
		class="btn btn-ghost btn-square btn-sm"
		aria-label={label}
		aria-expanded={shown}
		onfocus={() => (hover = true)}
		onblur={() => (hover = false)}
		onclick={(e) => {
			e.stopPropagation();
			open = !open;
		}}><Info size={16} /></button>
	{#if shown}
		<span role="note" class="absolute top-full {align === 'right' ? 'right-0' : 'left-0'} z-20 mt-2 w-64 rounded-lg border border-line bg-white p-3 text-left font-mono text-xs font-normal normal-case tracking-normal text-muted shadow-lg">{text}</span>
	{/if}
</span>
