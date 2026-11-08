<script lang="ts">
	import AnalyticsView from '#lib/components/analytics/AnalyticsView.svelte';

	let { data, form } = $props();

	// While SvelteKit switches pages this page is briefly handed the next page's data: keep showing the last data that
	// was the analytics page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if (data.team && 'scope' in data) last = data;
		return last;
	});
</script>

{#if shown.team && 'scope' in shown}
	<AnalyticsView data={shown} {form} />
{/if}
