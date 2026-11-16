<script lang="ts">
	import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip } from 'chart.js';
	import type { Series } from '#lib/dashboard.ts';

	Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip);

	let {
		series,
		label,
		color = '#0B1220',
		money = false
	}: { series: Series; label: string; color?: string; money?: boolean } = $props();

	let canvas: HTMLCanvasElement | undefined = $state();

	const ink = '#3A4658';
	const mono = { family: 'IBM Plex Mono', size: 10 };
	const eur = new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
	const fmt = (n: number) => (money ? eur.format(n) : String(n));
	const month = (m: string) => new Date(m + '-01T00:00:00').toLocaleDateString('en', { month: 'short' });

	$effect(() => {
		if (!canvas) return;
		const chart = new Chart(canvas, {
			type: 'bar',
			data: {
				labels: series.map((p) => month(p.month)),
				datasets: [{ label, data: series.map((p) => Number(p.value)), backgroundColor: color }]
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				animation: false,
				plugins: {
					legend: { display: false },
					tooltip: {
						callbacks: {
							title: (items) => series[items[0].dataIndex].month,
							label: (item) => fmt(item.parsed.y ?? 0)
						}
					}
				},
				scales: {
					x: { ticks: { color: ink, font: mono }, grid: { display: false } },
					y: {
						beginAtZero: true,
						ticks: { color: ink, font: mono, precision: 0, callback: (v) => fmt(Number(v)) },
						grid: { color: 'rgba(58,70,88,.12)' }
					}
				}
			}
		});
		return () => chart.destroy();
	});
</script>

<div class="h-44">
	<canvas bind:this={canvas} aria-label={label}>{label}</canvas>
</div>
