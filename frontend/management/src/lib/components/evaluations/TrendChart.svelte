<script lang="ts">
	import { fmt, type Point } from '#lib/evaluations.ts';

	/**
	 * A line chart of scores oldest to newest that fills the space it is given: it measures its box and draws at that exact size (so text
	 * and dots never stretch), at least `height` tall. Inside a flex column it takes the free height (`flex-1`). The points are spaced
	 * evenly by occurrence (evaluations come at irregular intervals, so a long gap would only squash the rest). The scale runs from 0 to
	 * the next whole number above the highest score.
	 */
	let { points, label, height = 176 }: { points: Point[]; label: string; height?: number } = $props();
	let w = $state(300);
	let h = $state(176);
	const padL = 30;
	const padR = 14;
	const padT = 26;
	const padB = 26;
	const top = $derived(Math.max(1, Math.ceil(Math.max(...points.map((p) => p.value), 1))));
	const ticks = $derived(top <= 6 ? Array.from({ length: top }, (_, i) => i + 1) : [Math.round(top / 2), top]);
	const y = (v: number) => padT + (1 - v / top) * Math.max(10, h - padT - padB);
	const xs = $derived(points.map((_, i) => (points.length === 1 ? (padL + w - padR) / 2 : padL + 16 + (i * (w - padL - padR - 32)) / (points.length - 1))));
</script>

<div class="relative w-full flex-1" style="min-height: {height}px" bind:clientWidth={w} bind:clientHeight={h}>
	<svg width={w} height={h} viewBox="0 0 {w} {h}" class="absolute inset-0 block" role="img" aria-label={label}>
		{#each ticks as g (g)}
			<line x1={padL - 4} x2={w - padR + 4} y1={y(g)} y2={y(g)} class="stroke-line" stroke-width="1" />
			<text x={padL - 10} y={y(g) + 4} text-anchor="end" class="fill-muted font-mono" font-size="12">{g}</text>
		{/each}
		{#if points.length > 1}<polyline points={points.map((p, i) => `${xs[i]},${y(p.value)}`).join(' ')} fill="none" class="stroke-club" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />{/if}
		{#each points as p, i (p.id)}
			<circle cx={xs[i]} cy={y(p.value)} r="4.5" class="fill-club stroke-white" stroke-width="2" />
			<text x={xs[i]} y={y(p.value) - 10} text-anchor="middle" class="fill-ink font-mono" font-size="13" font-weight="700">{fmt(p.value)}</text>
			<text x={xs[i]} y={h - 6} text-anchor="middle" class="fill-muted" font-size="11">{p.label}</text>
		{/each}
	</svg>
</div>
