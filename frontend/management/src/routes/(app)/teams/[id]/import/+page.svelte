<script lang="ts">
	import FixtureImportView from '#lib/components/fixtures/FixtureImportView.svelte';

	let { data } = $props();

	// While SvelteKit switches pages this page is briefly handed the next page's data: keep showing the last data that was
	// this page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if ('prefill' in data) last = data;
		return last;
	});
</script>

{#if 'prefill' in shown}
	<FixtureImportView data={shown} />
{/if}
