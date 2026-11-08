<script lang="ts">
	import TeamAddView from '#lib/components/teams/TeamAddView.svelte';

	let { data, form } = $props();

	// While SvelteKit switches pages this page is briefly handed the next page's data: keep showing the last data that was
	// this page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if ('positions' in data) last = data;
		return last;
	});
</script>

{#if 'positions' in shown}
	<TeamAddView data={shown} {form} />
{/if}
