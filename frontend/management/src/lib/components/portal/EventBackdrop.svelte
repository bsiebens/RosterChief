<script lang="ts">
	let { src = null }: { src?: string | null } = $props();
</script>

{#if src}
	<img class="absolute inset-0 -z-20 size-full object-cover grayscale" {src} alt="" />
	<div class="pointer-events-none absolute inset-0 -z-10 bg-linear-to-b from-(--event-backdrop,#172942)/85 to-(--event-backdrop,#172942)/95"></div>
{:else}
	<!-- No club event background: a quiet pattern so the card never reads as a flat black box. -->
	<div class="sample-stripes-dark pointer-events-none absolute inset-0 -z-20"></div>
{/if}
