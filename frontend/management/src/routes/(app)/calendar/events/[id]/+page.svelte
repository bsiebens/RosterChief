<script lang="ts">
	import EventDetailView from '#lib/components/events/EventDetailView.svelte';

	let { data, form } = $props();

	// While SvelteKit switches pages (back to the calendar) this page is briefly handed the other page's data: keep
	// showing the last data that held an event, so nothing reads a missing event while the page is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if (data.event) last = data;
		return last;
	});
</script>

{#if shown.event}
	<EventDetailView data={shown} {form} />
{/if}
