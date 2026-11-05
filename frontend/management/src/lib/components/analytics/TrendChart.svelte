<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { shortDate, signed, type Trend } from '#lib/analytics.ts';

	/**
	 * The running goal difference after each game, on a dark card. The server lays the points out in a 0–100 box
	 * (y grows downwards, `zero_y` is the zero line); the line stretches, the dots and hover targets are HTML on top.
	 */
	let { trend }: { trend: Trend } = $props();
	let hover = $state<number | null>(null);
	const hovered = $derived(hover === null ? null : trend.points[hover]);
	const games = $derived(trend.points.filter((p) => p.game));
</script>

<div class="relative mx-3 h-32" role="presentation" onmouseleave={() => (hover = null)}>
	<svg class="absolute inset-0 size-full overflow-visible" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={t('analytics.trend.title')}>
		<line x1="0" x2="100" y1={trend.zero_y} y2={trend.zero_y} class="stroke-on-dark-faint" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke" />
		<polygon points={trend.area} class="fill-club/20" />
		<polyline points={trend.line} class="fill-none stroke-club" stroke-width="2.5" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
	</svg>
	{#each trend.points as point, i (i)}
		{#if point.game}
			<button
				type="button"
				class="absolute flex size-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full focus-visible:outline-2 focus-visible:outline-white"
				style="left: {point.x}%; top: {point.y}%"
				aria-label="{point.game.opponent ?? point.game.title} · {shortDate(point.game.start)}: {signed(point.value)}"
				onmouseenter={() => (hover = i)}
				onfocus={() => (hover = i)}
				onblur={() => (hover = null)}>
				<span class="size-2.5 rounded-full border-2 border-club {i === trend.points.length - 1 || hover === i ? 'bg-white' : 'bg-ink'}"></span>
			</button>
		{/if}
	{/each}
	{#if hovered?.game}
		<div class="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg bg-white px-3 py-2 text-xs whitespace-nowrap text-ink shadow-lg" style="left: {Math.min(85, Math.max(15, hovered.x))}%; top: calc({hovered.y}% - 0.75rem)">
			<p class="font-display text-sm font-bold">{hovered.game.opponent ?? hovered.game.title}</p>
			<p class="text-muted">{shortDate(hovered.game.start)} · {hovered.game.is_home_game ? t('analytics.home') : t('analytics.away')}</p>
			<p class="mt-1 flex gap-4"><span class="font-mono font-semibold">{hovered.game.score_for}–{hovered.game.score_against}</span><span class="ml-auto">{t('analytics.trend.running')} <span class="font-mono font-semibold">{signed(hovered.value)}</span></span></p>
		</div>
	{/if}
</div>
<div class="mx-3 mt-2 flex justify-between font-mono text-[11px] text-on-dark">
	<span>{t('analytics.trend.start')}</span>
	{#if games.length}<span>{shortDate(games[games.length - 1].game!.start)}</span>{/if}
</div>
