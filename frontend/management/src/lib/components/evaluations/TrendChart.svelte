<script lang="ts">
	import { fmt, type Point } from '#lib/evaluations.ts';

	/**
	 * A small line chart of scores oldest to newest. The points are spaced evenly by occurrence (evaluations come at irregular
	 * intervals, so a long gap would only squash the rest). The scale runs from 0 to the next whole number above the highest score.
	 */
	let { points, label, height = 150 }: { points: Point[]; label: string; height?: number } = $props();
	const W = 300;
	const top = $derived(Math.max(1, Math.ceil(Math.max(...points.map((p) => p.value), 1))));
	const ticks = $derived(top <= 6 ? Array.from({ length: top }, (_, i) => i + 1) : [Math.round(top / 2), top]);
	const base = $derived(height - 30);
	const y = (v: number) => base - (v / top) * (base - 20);
	const xs = $derived(points.map((_, i) => (points.length === 1 ? W / 2 : 50 + (i * (W - 80)) / (points.length - 1))));
</script>

<svg viewBox="0 0 {W} {height}" class="w-full" role="img" aria-label={label}>
	{#each ticks as g (g)}
		<line x1="28" x2={W - 8} y1={y(g)} y2={y(g)} class="stroke-line" stroke-width="1" />
		<text x="20" y={y(g) + 3.5} text-anchor="end" class="fill-muted font-mono" font-size="10">{g}</text>
	{/each}
	{#if points.length > 1}<polyline points={points.map((p, i) => `${xs[i]},${y(p.value)}`).join(' ')} fill="none" class="stroke-club" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />{/if}
	{#each points as p, i (p.id)}
		<circle cx={xs[i]} cy={y(p.value)} r="4.5" class="fill-club stroke-white" stroke-width="2" />
		<text x={xs[i]} y={y(p.value) - 10} text-anchor="middle" class="fill-ink font-mono" font-size="11" font-weight="700">{fmt(p.value)}</text>
		<text x={xs[i]} y={height - 6} text-anchor="middle" class="fill-muted" font-size="10">{p.label}</text>
	{/each}
</svg>
