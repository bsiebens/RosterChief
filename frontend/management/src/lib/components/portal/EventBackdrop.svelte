<script lang="ts">
	let { src = null }: { src?: string | null } = $props();
</script>

{#if src}
	<img class="absolute inset-0 -z-20 size-full object-cover grayscale" {src} alt="" />
	<div class="pointer-events-none absolute inset-0 -z-10 bg-linear-to-b from-(--event-backdrop)/75 to-(--event-backdrop)"></div>
{/if}
