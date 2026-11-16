<script lang="ts">
	import { UserRoundPlus } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { Overview } from '#lib/overview.ts';

	/** Stacked bars: new members (dark) on returning ones (club colour), last 12 months. */
	let { months }: { months: Overview['signups'] } = $props();
	// Drawn at the size it is shown (measured), so the axis text keeps its real size instead of scaling up with the card.
	let W = $state(560);
	const H = 190;
	const L = 30;
	const B = 24;
	const top = $derived(Math.max(1, ...months.map((m) => m.new + m.returning)));
	// Whole-number gridlines: step 1 up to 5, else the smallest "nice" step that keeps it to five lines or fewer.
	const stepSize = $derived(top <= 5 ? 1 : ([2, 5, 10, 20, 25, 50, 100, 200, 500, 1000].find((s) => top / s <= 5) ?? Math.ceil(top / 5)));
	const max = $derived(top <= 5 ? 5 : Math.ceil(top / stepSize) * stepSize);
	const ticks = $derived(Array.from({ length: Math.floor(max / stepSize) + 1 }, (_, i) => i * stepSize));
	const y = (v: number) => H - B - (v / max) * (H - B - 8);
	const step = $derived((W - L) / Math.max(months.length, 1));
	const bar = $derived(Math.min(34, step * 0.62));
	const label = (m: string) => new Intl.DateTimeFormat(getLang(), { month: 'short' }).format(new Date(`${m}-01T00:00:00`));
	let hover = $state<number | null>(null);
	const hovered = $derived(hover === null ? null : months[hover]);
	const tipLeft = $derived(hover === null ? 0 : Math.min(88, Math.max(12, ((L + hover * step + step / 2) / W) * 100)));
	const fullLabel = (m: string) => new Intl.DateTimeFormat(getLang(), { month: 'long', year: 'numeric' }).format(new Date(`${m}-01T00:00:00`));
	const empty = $derived(months.every((m) => m.new + m.returning === 0));
</script>

<div class="card">
	<div class="card-h"><UserRoundPlus size={17} class="shrink-0" aria-hidden="true" /><h2>{t('overview.signups.title')}</h2></div>
	<div class="grid gap-2 p-5">
	<p class="text-sm text-muted">{t('overview.signups.sub')}</p>
	{#if empty}
		<p class="py-8 text-center text-sm text-muted">{t('overview.signups.empty')}</p>
	{:else}
		<div class="relative" role="presentation" bind:clientWidth={W} onmouseleave={() => (hover = null)}>
		{#if hovered}
			<div class="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-lg bg-ink px-3 py-2 text-xs whitespace-nowrap text-white shadow-lg" style="left: {tipLeft}%">
				<p class="mb-1 font-display text-sm font-bold">{fullLabel(hovered.month)}</p>
				<p class="flex items-center gap-2"><span class="h-2.5 w-2.5 rounded-sm bg-white"></span>{t('overview.signups.new')}<span class="ml-auto pl-4 font-mono font-semibold">{hovered.new}</span></p>
				<p class="flex items-center gap-2"><span class="h-2.5 w-2.5 rounded-sm bg-club"></span>{t('overview.signups.returning')}<span class="ml-auto pl-4 font-mono font-semibold">{hovered.returning}</span></p>
				<p class="mt-1 flex border-t border-white/20 pt-1">{t('overview.signups.total')}<span class="ml-auto pl-4 font-mono font-semibold">{hovered.new + hovered.returning}</span></p>
			</div>
		{/if}
		<svg width={W} height={H} viewBox="0 0 {W} {H}" class="block" role="img" aria-label={t('overview.signups.title')}>
			{#each ticks as v (v)}
				<line x1={L} x2={W} y1={y(v)} y2={y(v)} class="stroke-line" stroke-width="1" />
				<text x={L - 6} y={y(v) + 3.5} text-anchor="end" class="fill-muted font-mono" font-size="11">{v}</text>
			{/each}
			{#each months as m, i (m.month)}
				{@const x = L + i * step + (step - bar) / 2}
				{#if m.new}<rect {x} y={y(m.new)} width={bar} height={y(0) - y(m.new)} rx="2" class="fill-ink" />{/if}
				{#if m.returning}<rect {x} y={y(m.new + m.returning)} width={bar} height={y(m.new) - y(m.new + m.returning)} rx="2" class="fill-club" />{/if}
				<text x={x + bar / 2} y={H - 8} text-anchor="middle" class="fill-muted" font-size="11">{label(m.month)}</text>
				{#if hover === i}<rect x={L + i * step} y="0" width={step} height={H - B} class="fill-ink/5" />{/if}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<rect
					x={L + i * step}
					y="0"
					width={step}
					height={H - B}
					fill="transparent"
					role="img"
					tabindex="0"
					aria-label="{fullLabel(m.month)}: {m.new} {t('overview.signups.new')}, {m.returning} {t('overview.signups.returning')}"
					onmouseenter={() => (hover = i)}
					onfocus={() => (hover = i)}
					onblur={() => (hover = null)} />
			{/each}
		</svg>
		</div>
		<div class="flex justify-center gap-5 text-sm text-muted">
			<span class="inline-flex items-center gap-2"><span class="h-3 w-6 rounded-sm bg-ink"></span>{t('overview.signups.new')}</span>
			<span class="inline-flex items-center gap-2"><span class="h-3 w-6 rounded-sm bg-club"></span>{t('overview.signups.returning')}</span>
		</div>
	{/if}
	</div>
</div>
