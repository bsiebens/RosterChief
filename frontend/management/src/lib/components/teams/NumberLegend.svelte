<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { TILE_STYLE, type NumberStatus } from '#lib/teams/status.ts';

	let { statuses }: { statuses: NumberStatus[] } = $props();
</script>

<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
	{#each statuses as s (s)}
		<li class="flex items-center gap-1.5"><span class="inline-block h-3.5 w-3.5 rounded border" style={TILE_STYLE[s]}></span> {t(`teams.num.${s}`)}</li>
	{/each}
</ul>
