<script lang="ts">
	import FamilyView from '#lib/components/families/FamilyView.svelte';

	let { data, form } = $props();

	// While SvelteKit switches pages this page is briefly handed the next page's data: keep showing the last data that was
	// this page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if (data.family) last = data;
		return last;
	});
</script>

{#if shown.family}
	<FamilyView data={shown} {form} />
{/if}
