<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { ForAgainst } from '#lib/analytics.ts';

	/** Grouped bars per bucket (a period or a 5-minute stretch of the game clock): goals for (ink) next to goals against (club colour). */
	let { rows, axisLabel, tipLabel, ariaLabel }: { rows: ForAgainst[]; axisLabel: (row: ForAgainst) => string; tipLabel: (row: ForAgainst) => string; ariaLabel: string } = $props();
	const W = 560;
	const H = 170;
	const L = 26;
	const B = 22;
	const top = $derived(Math.max(1, ...rows.map((r) => Math.max(r.goals_for, r.goals_against))));
	const stepSize = $derived(top <= 5 ? 1 : ([2, 5, 10, 20, 25, 50].find((s) => top / s <= 5) ?? Math.ceil(top / 5)));
	const max = $derived(top <= 5 ? Math.max(top, 2) : Math.ceil(top / stepSize) * stepSize);
	const ticks = $derived(Array.from({ length: Math.floor(max / stepSize) + 1 }, (_, i) => i * stepSize));
	const y = (v: number) => H - B - (v / max) * (H - B - 8);
	const step = $derived((W - L) / Math.max(rows.length, 1));
	const bar = $derived(Math.min(20, step * 0.32));
	let hover = $state<number | null>(null);
	const hovered = $derived(hover === null ? null : rows[hover]);
	const tipLeft = $derived(hover === null ? 0 : Math.min(85, Math.max(15, ((L + hover * step + step / 2) / W) * 100)));
</script>

<div class="relative" role="presentation" onmouseleave={() => (hover = null)}>
	{#if hovered}
		<div class="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-lg bg-ink px-3 py-2 text-xs whitespace-nowrap text-white shadow-lg" style="left: {tipLeft}%">
			<p class="mb-1 font-display text-sm font-bold">{tipLabel(hovered)}</p>
			<p class="flex items-center gap-2"><span class="h-2.5 w-2.5 rounded-sm bg-white"></span>{t('analytics.goals_for')}<span class="ml-auto pl-4 font-mono font-semibold">{hovered.goals_for}</span></p>
			<p class="flex items-center gap-2"><span class="h-2.5 w-2.5 rounded-sm bg-club"></span>{t('analytics.goals_against')}<span class="ml-auto pl-4 font-mono font-semibold">{hovered.goals_against}</span></p>
		</div>
	{/if}
	<svg viewBox="0 0 {W} {H}" class="w-full" role="img" aria-label={ariaLabel}>
		{#each ticks as v (v)}
			<line x1={L} x2={W} y1={y(v)} y2={y(v)} class="stroke-line" stroke-width="1" />
			<text x={L - 6} y={y(v) + 3.5} text-anchor="end" class="fill-muted font-mono" font-size="10">{v}</text>
		{/each}
		{#each rows as row, i (row.label)}
			{@const cx = L + i * step + step / 2}
			{#if hover === i}<rect x={L + i * step} y="0" width={step} height={H - B} class="fill-ink/5" />{/if}
			{#if row.goals_for}<rect x={cx - bar - 1} y={y(row.goals_for)} width={bar} height={y(0) - y(row.goals_for)} rx="2" class="fill-ink" />{/if}
			{#if row.goals_against}<rect x={cx + 1} y={y(row.goals_against)} width={bar} height={y(0) - y(row.goals_against)} rx="2" class="fill-club" />{/if}
			<text x={cx} y={H - 7} text-anchor="middle" class="fill-muted font-mono" font-size="10">{axisLabel(row)}</text>
			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<rect
				x={L + i * step}
				y="0"
				width={step}
				height={H - B}
				fill="transparent"
				role="img"
				tabindex="0"
				aria-label="{tipLabel(row)}: {row.goals_for} {t('analytics.goals_for')}, {row.goals_against} {t('analytics.goals_against')}"
				onmouseenter={() => (hover = i)}
				onfocus={() => (hover = i)}
				onblur={() => (hover = null)} />
		{/each}
	</svg>
</div>
