<script lang="ts">
	import './layout.css';
	import { setLang } from '#lib/i18n.svelte.ts';

	let { data, children } = $props();

	// Once for the render in progress (also on the server), then again whenever the language changes.
	// svelte-ignore state_referenced_locally
	setLang(data.lang);
	$effect.pre(() => {
		setLang(data.lang);
	});
</script>

{@render children()}
