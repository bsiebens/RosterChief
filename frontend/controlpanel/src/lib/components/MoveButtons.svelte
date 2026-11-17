<script lang="ts">
	import { ChevronDown, ChevronUp } from '@lucide/svelte';

	/** Move an item one place up or down in a list the platform orders by hand (plans, billing cycles). */
	let { label, canUp, canDown, busy = false, onmove }: { label: string; canUp: boolean; canDown: boolean; busy?: boolean; onmove: (direction: -1 | 1) => void } = $props();
</script>

<span class="inline-flex" role="group" aria-label="Order of {label}">
	<button type="button" class="btn btn-ghost btn-xs btn-square" disabled={!canUp || busy} aria-label="Move {label} up" title="Move up" onclick={() => onmove(-1)}><ChevronUp size={15} aria-hidden="true" /></button>
	<button type="button" class="btn btn-ghost btn-xs btn-square" disabled={!canDown || busy} aria-label="Move {label} down" title="Move down" onclick={() => onmove(1)}><ChevronDown size={15} aria-hidden="true" /></button>
</span>
