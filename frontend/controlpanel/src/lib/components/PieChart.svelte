<script lang="ts">
	import { ArcElement, Chart, Legend, PieController, Tooltip } from 'chart.js';

	Chart.register(PieController, ArcElement, Tooltip, Legend);

	let { slices, label }: { slices: { label: string; value: number }[]; label: string } = $props();

	let canvas: HTMLCanvasElement | undefined = $state();
	// Neutral first, then the theme's semantic colours; enough for a handful of member types.
	const COLORS = ['#0B1220', '#14B8E8', '#14A05A', '#F0A22E', '#E4002B', '#8B95A4', '#3A4658', '#B00021'];
	const ink = '#3A4658';

	$effect(() => {
		if (!canvas) return;
		const chart = new Chart(canvas, {
			type: 'pie',
			data: { labels: slices.map((s) => s.label), datasets: [{ label, data: slices.map((s) => s.value), backgroundColor: slices.map((_, i) => COLORS[i % COLORS.length]), borderWidth: 1, borderColor: '#fff' }] },
			options: {
				responsive: true,
				maintainAspectRatio: false,
				animation: false,
				plugins: { legend: { position: 'right', labels: { color: ink, font: { family: 'IBM Plex Mono', size: 11 } } } }
			}
		});
		return () => chart.destroy();
	});
</script>

<div class="h-44">
	<canvas bind:this={canvas} aria-label={label}>{label}</canvas>
</div>
