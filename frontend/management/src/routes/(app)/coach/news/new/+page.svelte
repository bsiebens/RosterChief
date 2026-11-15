<script lang="ts">
	import CoachNewsEditor from '#lib/components/coach/CoachNewsEditor.svelte';

	let { data } = $props();
</script>

<CoachNewsEditor item={null} teams={data.teams} canPublishNew={data.canPublish} />
