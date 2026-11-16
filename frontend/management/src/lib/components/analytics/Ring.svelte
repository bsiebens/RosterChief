<script lang="ts">
	import { pct } from '#lib/analytics.ts';

	/** A percentage as a ring (special teams). `value` null: no chances yet, an empty ring with a dash. */
	let { value, label, detail }: { value: number | null; label: string; detail: string } = $props();
</script>

<div class="text-center">
	<div class="relative mx-auto size-28">
		<svg class="size-full -rotate-90" viewBox="0 0 100 100" aria-hidden="true">
			<circle cx="50" cy="50" r="44" fill="none" class="stroke-rule" stroke-width="9" />
			{#if value}<circle cx="50" cy="50" r="44" fill="none" class="stroke-ink" stroke-width="9" stroke-linecap="round" pathLength="100" stroke-dasharray="{value} 100" />{/if}
		</svg>
		<span class="absolute inset-0 flex items-center justify-center font-display text-2xl font-extrabold text-ink">{pct(value)}</span>
	</div>
	<div class="mt-2 text-[15px] font-bold text-ink">{label}</div>
	<div class="text-xs text-muted">{detail}</div>
</div>
