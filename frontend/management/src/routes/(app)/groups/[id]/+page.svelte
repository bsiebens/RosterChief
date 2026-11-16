<script lang="ts">
	import GroupView from '#lib/components/families/GroupView.svelte';

	let { data, form } = $props();

	// While SvelteKit switches pages this page is briefly handed the next page's data: keep showing the last data that was
	// this page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if (data.group) last = data;
		return last;
	});
</script>

{#if shown.group}
	<GroupView data={shown} {form} />
{/if}
