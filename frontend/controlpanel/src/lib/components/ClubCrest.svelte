<script lang="ts">
	import { initials } from '#lib/clubs.ts';

	// The club's own logo when it has one; its initials otherwise (also if the image fails to load).
	let { name, logo = null, size = 40 }: { name: string; logo?: string | null; size?: number } = $props();

	let failed = $state(false);
	$effect(() => {
		logo; // a different logo gets a fresh chance to load
		failed = false;
	});
</script>

{#if logo && !failed}
	<img class="shrink-0 rounded-sm bg-white object-contain" src={logo} alt="{name} logo" width={size} height={size} style="width:{size}px;height:{size}px" loading="lazy" onerror={() => (failed = true)} />
{:else}
	<span class="crest flex shrink-0 items-center justify-center bg-ink font-display font-extrabold text-white" style="width:{size}px;height:{size}px;font-size:{Math.round(size * 0.32)}px" aria-hidden="true">{initials(name)}</span>
{/if}
