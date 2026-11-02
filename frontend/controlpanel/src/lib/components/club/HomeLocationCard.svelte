<script lang="ts">
	import { MapPin } from '@lucide/svelte';
</script>

<!-- Placeholder: club locations arrive with the events module. The box is here so the page layout is final. -->
<div class="card flex flex-col">
	<div class="flex items-center justify-between border-b border-line px-4 py-3">
		<span class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase"><MapPin size={16} aria-hidden="true" /> Home location</span>
		<button class="btn btn-outline btn-sm" type="button" disabled title="Available once locations exist">Set home location</button>
	</div>
	<div class="flex flex-col gap-4 p-4">
		<div class="flex min-h-40 flex-col items-center justify-center gap-2 rounded border border-dashed border-edge bg-subhead text-center">
			<MapPin size={28} class="text-dim" aria-hidden="true" />
			<p class="font-mono text-[11px] text-muted">No home location set</p>
		</div>
		<p class="text-sm text-muted">Once club locations exist, the home ground chosen here lets events at that location be recognised as home games.</p>
	</div>
</div>
