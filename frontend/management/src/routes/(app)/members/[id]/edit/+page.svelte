<script lang="ts">
	import MemberEditView from '#lib/components/members/MemberEditView.svelte';

	let { data, form } = $props();

	// While SvelteKit switches pages this page is briefly handed the next page's data: keep showing the last data that was
	// this page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if ('hasLogin' in data) last = data;
		return last;
	});
</script>

{#if 'hasLogin' in shown}
	<MemberEditView data={shown} {form} />
{/if}
