<script lang="ts">
	import { ArrowDownRight, ArrowRight, ArrowUpRight } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { shortDate, type Progress } from '#lib/analytics.ts';

	/**
	 * A season-to-date line over the games, with the change from the first half to the second half of the season.
	 * `lowerIsBetter` flips the colour of the change (penalty minutes); `max` fixes the top of the scale (100 for a percentage).
	 */
	let { title, data, format, lowerIsBetter = false, max = null }: { title: string; data: Progress; format: (v: number | null) => string; lowerIsBetter?: boolean; max?: number | null } = $props();
	let hover = $state<number | null>(null);

	const top = $derived(max ?? Math.max(1, ...data.points.map((p) => p.value)) * 1.2);
	const xs = (i: number) => (data.points.length < 2 ? 50 : (i / (data.points.length - 1)) * 100);
	const ys = (v: number) => 100 - (Math.min(v, top) / top) * 100;
	const line = $derived(data.points.map((p, i) => `${xs(i)},${ys(p.value)}`).join(' '));
	// A label per dot while there is room for them; with many games every few dots (and always the last).
	const labelStep = $derived(Math.max(1, Math.ceil(data.points.length / 10)));
	const labelled = (i: number) => i % labelStep === 0 || i === data.points.length - 1;
	const hovered = $derived(hover === null ? null : data.points[hover]);
	const direction = $derived(data.change === null || Math.abs(data.change) < 0.05 ? 0 : data.change > 0 ? 1 : -1);
	const good = $derived(direction === 0 ? null : lowerIsBetter ? direction < 0 : direction > 0);
	const Arrow = $derived(direction > 0 ? ArrowUpRight : direction < 0 ? ArrowDownRight : ArrowRight);
</script>

<div class="flex flex-col gap-3 rounded-xl bg-paper p-4">
	<div class="flex flex-wrap items-start justify-between gap-2">
		<div>
			<p class="text-xs text-muted">{title}</p>
			<p class="mt-1 font-mono text-2xl leading-none font-semibold text-ink">{format(data.current)}</p>
		</div>
		{#if data.change !== null}
			<span class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold {good === null ? 'border-line bg-white text-slate' : good ? 'border-ok-border bg-ok-bg text-ok-text' : 'border-danger-border bg-danger-bg text-danger'}">
				<Arrow size={14} aria-hidden="true" />
				{good === null ? t('analytics.progress.same') : good ? t('analytics.progress.better') : t('analytics.progress.worse')}
			</span>
		{/if}
	</div>

	{#if data.points.length}
		<div class="relative mx-2 mt-5 h-28" role="presentation" onmouseleave={() => (hover = null)}>
			<svg class="absolute inset-0 size-full overflow-visible" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={title}>
				<line x1="0" x2="100" y1="100" y2="100" class="stroke-ink" stroke-width="1.5" vector-effect="non-scaling-stroke" />
				<line x1="0" x2="100" y1="50" y2="50" class="stroke-line" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke" />
				{#if data.points.length > 1}<polyline points={line} class="fill-none stroke-ink" stroke-width="2.5" stroke-linejoin="round" vector-effect="non-scaling-stroke" />{/if}
			</svg>
			{#each data.points as point, i (i)}
				<button
					type="button"
					class="absolute flex size-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full focus-visible:outline-2 focus-visible:outline-ink"
					style="left: {xs(i)}%; top: {ys(point.value)}%"
					aria-label={t('analytics.progress.tip', { game: point.game.opponent ?? point.game.title, value: format(point.value), game_value: format(point.game_value) })}
					onmouseenter={() => (hover = i)}
					onfocus={() => (hover = i)}
					onblur={() => (hover = null)}>
					<span class="size-2.5 rounded-full border-2 border-ink {i === data.points.length - 1 || hover === i ? 'bg-club' : 'bg-white'}"></span>
					{#if labelled(i) && hover !== i}<span class="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 font-mono text-[10px] leading-none font-semibold whitespace-nowrap text-slate" aria-hidden="true">{format(point.value)}</span>{/if}
				</button>
			{/each}
			{#if hovered}
				<div class="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg bg-ink px-3 py-2 text-xs whitespace-nowrap text-white shadow-lg" style="left: {Math.min(80, Math.max(20, xs(hover ?? 0)))}%; top: calc({ys(hovered.value)}% - 0.75rem)">
					<p class="font-display text-sm font-bold">{hovered.game.opponent ?? hovered.game.title}</p>
					<p class="font-mono text-[10px] text-white/70">{shortDate(hovered.game.start)}</p>
					<p class="mt-1 flex gap-4"><span>{t('analytics.progress.sofar', { value: format(hovered.value) })}</span><span class="ml-auto font-mono">{format(hovered.game_value)} <span class="text-white/70">{t('analytics.progress.this')}</span></span></p>
				</div>
			{/if}
		</div>
		<div class="mx-2 flex justify-between font-mono text-[11px] text-muted">
			<span>{shortDate(data.points[0].game.start)}</span>
			{#if data.points.length > 1}<span>{shortDate(data.points[data.points.length - 1].game.start)}</span>{/if}
		</div>
	{/if}

	<div class="text-xs text-slate">
		{#if data.change !== null}
			<p>{t('analytics.progress.first', { v: format(data.first) })}</p>
			<p>{t('analytics.progress.second', { v: format(data.second) })}</p>
		{:else}
			<p>{t('analytics.progress.few')}</p>
		{/if}
	</div>
</div>
