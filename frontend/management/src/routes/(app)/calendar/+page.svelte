<script lang="ts">
	import CalendarView from '#lib/components/events/CalendarView.svelte';

	let { data } = $props();

	// While SvelteKit switches pages (back from an event) this page is briefly handed the other page's data: keep
	// showing the last data that held the event list, so nothing reads a missing list while the page is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if (data.events) last = data;
		return last;
	});
</script>

{#if shown.events}
	<CalendarView data={shown} />
{/if}
