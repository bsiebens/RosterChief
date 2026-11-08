<script lang="ts">
	import MemberHistoryView from '#lib/components/members/MemberHistoryView.svelte';

	let { data } = $props();

	// While SvelteKit switches pages this page is briefly handed the next page's data: keep showing the last data that was
	// this page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if ('items' in data) last = data;
		return last;
	});
</script>

{#if 'items' in shown}
	<MemberHistoryView data={shown} />
{/if}
