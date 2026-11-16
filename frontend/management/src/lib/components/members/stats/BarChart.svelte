<script lang="ts">
	/**
	 * Vertical bars with the number on top, an optional stack of segments per bar and a hover/focus tooltip. Hand-written
	 * HTML/CSS like the analytics charts: ink and club red by default. `max` fixes the scale (e.g. 100 for percentages).
	 */
	type Segment = { value: number; cls: string };
	type Item = { key: string; label: string; segments: Segment[]; tip: string[]; top?: string };
	let { items, label, max, height = 'h-36', empty = '' }: { items: Item[]; label: string; max?: number; height?: string; empty?: string } = $props();

	const total = (item: Item) => item.segments.reduce((sum, s) => sum + s.value, 0);
	const scale = $derived(max ?? Math.max(1, ...items.map(total)));
	const dense = $derived(items.length > 12);
	let hover = $state<number | null>(null);
	const side = (i: number) => (i < 2 ? 'left-0' : i > items.length - 3 ? 'right-0' : 'left-1/2 -translate-x-1/2');
</script>

{#if items.length === 0}
	<p class="py-6 text-center text-sm text-muted">{empty}</p>
{:else}
	<div role="group" aria-label={label} class="flex items-end gap-1 pt-6 {height}" onmouseleave={() => (hover = null)}>
		{#each items as item, i (item.key)}
			{@const sum = total(item)}
			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<div class="relative flex h-full min-w-0 flex-1 flex-col justify-end rounded-md transition-colors {hover === i ? 'bg-paper' : ''}" role="img" tabindex="0" aria-label={item.tip.join(', ')} onmouseenter={() => (hover = i)} onfocus={() => (hover = i)} onblur={() => (hover = null)}>
				<span class="mb-0.5 text-center font-display leading-none font-extrabold text-ink {dense ? 'text-[10px]' : 'text-sm'} {sum === 0 && !item.top ? 'text-dim' : ''}">{item.top ?? sum}</span>
				<div class="mx-auto flex w-full max-w-10 flex-col-reverse overflow-hidden rounded-t-md" style="height: {Math.max((sum / scale) * 100, 2)}%; min-height: 2px">
					{#if sum === 0}
						<div class="h-full w-full bg-rule"></div>
					{:else}
						{#each item.segments as seg, j (j)}
							{#if seg.value > 0}<div class="w-full {seg.cls} {hover === i ? 'opacity-100' : 'opacity-90'}" style="height: {(seg.value / sum) * 100}%"></div>{/if}
						{/each}
					{/if}
				</div>
				{#if hover === i}
					<div class="pointer-events-none absolute bottom-full z-10 mb-1 rounded-lg bg-ink px-3 py-2 text-xs whitespace-nowrap text-white shadow-lg {side(i)}">
						{#each item.tip as line, k (k)}<p class={k === 0 ? 'font-display text-sm font-bold' : 'text-on-dark'}>{line}</p>{/each}
					</div>
				{/if}
			</div>
		{/each}
	</div>
	<div class="mt-1 flex gap-1" aria-hidden="true">
		{#each items as item, i (item.key)}<span class="min-w-0 flex-1 truncate text-center font-mono text-muted {dense ? 'text-[9px]' : 'text-[11px]'}">{dense && i % 2 === 1 ? '' : item.label}</span>{/each}
	</div>
{/if}
