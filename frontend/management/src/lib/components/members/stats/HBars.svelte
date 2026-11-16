<script lang="ts">
	/** Labelled horizontal bars with the figure at the end; `pct` is the 0–100 length, `cls` the bar colour. Hover or focus highlights a row. */
	type Row = { key: string; label: string; value: string; pct: number; cls?: string; sub?: string };
	let { rows, label }: { rows: Row[]; label: string } = $props();
	let hover = $state<number | null>(null);
</script>

<ul class="grid gap-2.5" aria-label={label} onmouseleave={() => (hover = null)}>
	{#each rows as row, i (row.key)}
		<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
		<li class="grid gap-1 rounded-lg px-1 py-0.5 transition-colors {hover === i ? 'bg-paper' : ''}" tabindex="0" title={row.sub ? `${row.label}: ${row.sub}` : row.label} onmouseenter={() => (hover = i)} onfocus={() => (hover = i)} onblur={() => (hover = null)}>
			<div class="flex items-baseline justify-between gap-2 text-sm">
				<span class="min-w-0 truncate font-semibold text-ink">{row.label}</span>
				<span class="shrink-0 font-display text-base font-extrabold text-ink">{row.value}</span>
			</div>
			<div class="h-2 overflow-hidden rounded-full bg-rule" aria-hidden="true"><div class="h-full rounded-full {row.cls ?? 'bg-ink'}" style="width: {Math.max(row.pct, row.pct > 0 ? 2 : 0)}%"></div></div>
			{#if row.sub}<p class="text-xs text-muted">{row.sub}</p>{/if}
		</li>
	{/each}
</ul>
