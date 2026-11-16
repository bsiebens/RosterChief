<script lang="ts">
	import PushTabs from '#lib/components/push/PushTabs.svelte';

	let { children } = $props();
</script>

<div>
	<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Push</h1>
	<p class="mt-1.5 font-mono text-[11px] text-muted">Push notification delivery: how healthy it is, a sandbox to test safely, and the provider configuration.</p>
</div>

<PushTabs />

{@render children()}
