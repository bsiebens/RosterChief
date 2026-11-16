<script lang="ts">
	import CoachNewsEditor from '#lib/components/coach/CoachNewsEditor.svelte';

	let { data } = $props();
</script>

<CoachNewsEditor item={data.item} teams={data.teams} />
