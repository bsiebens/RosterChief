<script lang="ts">
	import TeamPageView from '#lib/components/teams/TeamPageView.svelte';

	let { data, form } = $props();

	// While SvelteKit switches pages (back from analytics, import, ...) this page is briefly handed the other page's data:
	// keep showing the last data that was the team page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if (data.team && 'positions' in data) last = data;
		return last;
	});
</script>

{#if shown.team && 'positions' in shown}
	<TeamPageView data={shown} {form} />
{/if}
