<script lang="ts">
	import './layout.css';
	import { setLang } from '#lib/i18n.svelte.ts';
	import { onMount } from 'svelte';
	import { startPwa } from '#lib/pwa.svelte.ts';

	let { data, children } = $props();
	onMount(() => { if (data.mode === 'club') return startPwa(); });

	// Once for the render in progress (also on the server), then again whenever the language changes.
	// svelte-ignore state_referenced_locally
	setLang(data.lang);
	$effect.pre(() => {
		setLang(data.lang);
	});
</script>

<svelte:head>
	{#if data.mode === 'club'}
		<link rel="manifest" href="/manifest.webmanifest" />
		<meta name="apple-mobile-web-app-capable" content="yes" />
		{#if !data.branding?.icons.apple_touch}<link rel="apple-touch-icon" href="/images/app-icon-192.png" />{/if}
		<meta name="theme-color" content={data.branding?.primary_color || '#0f1a2e'} />
	{/if}
</svelte:head>

{@render children()}
