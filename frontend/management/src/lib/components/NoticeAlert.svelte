<script lang="ts">
	import Alert from '#lib/components/Alert.svelte';

	/** An inline success message that goes away by itself; `onhide` lets the owner clear the message. */
	let { message, onhide, seconds = 4 }: { message: string; onhide: () => void; seconds?: number } = $props();
	$effect(() => {
		if (!message) return;
		const timer = setTimeout(onhide, seconds * 1000);
		return () => clearTimeout(timer);
	});
</script>

{#if message}<Alert kind="success">{message}</Alert>{/if}
