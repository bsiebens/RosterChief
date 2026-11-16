<script lang="ts">
	import type { Snippet } from 'svelte';

	/** The secondary actions of a detail page's side card: a bordered list of `ActionRow`s (the one primary action stays a solid button above it). */
	let { children }: { children: Snippet } = $props();
</script>

<div class="divide-y divide-rule overflow-hidden rounded-lg border border-line">{@render children()}</div>
