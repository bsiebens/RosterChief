<script lang="ts">
	import { Wallet } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Overview } from '#lib/overview.ts';

	let { fees }: { fees: NonNullable<Overview['fees']> } = $props();
	const KEYS = ['paid', 'partially_paid', 'unpaid', 'waived'] as const;
	const colour: Record<(typeof KEYS)[number], string> = { paid: 'var(--color-ok)', partially_paid: 'var(--color-warn)', unpaid: 'var(--color-danger)', waived: 'var(--color-dim)' };
	const total = $derived(KEYS.reduce((s, k) => s + fees[k], 0));
	let active = $state<(typeof KEYS)[number] | null>(null);
	const pct = (n: number) => (total ? Math.round((n * 100) / total) : 0);
	const R = 46;
	const C = 2 * Math.PI * R;
	const arcs = $derived(
		KEYS.reduce<{ key: (typeof KEYS)[number]; len: number; off: number }[]>((acc, key) => {
			const used = acc.reduce((s, a) => s + a.len, 0);
			return [...acc, { key, len: total ? (fees[key] / total) * C : 0, off: used }];
		}, [])
	);
</script>

<div class="card card-body">
	<h2 class="card-title flex items-center gap-2"><Wallet size={16} class="shrink-0" aria-hidden="true" /> {t('overview.fees.title')}</h2>
	{#if !total}
		<p class="text-sm text-muted">{t('overview.fees.empty')}</p>
	{:else}
		<div class="flex flex-wrap items-center gap-5">
			<div class="relative h-36 w-36 shrink-0">
			<svg viewBox="0 0 120 120" class="h-36 w-36 -rotate-90" role="img" aria-label={t('overview.fees.title')}>
				<circle cx="60" cy="60" r={R} fill="none" class="stroke-rule" stroke-width="12" />
				{#each arcs as a (a.key)}
					{#if a.len}
						<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
						<circle
							cx="60"
							cy="60"
							r={R}
							fill="none"
							stroke={colour[a.key]}
							stroke-width={active === a.key ? 15 : 12}
							stroke-dasharray="{a.len} {C - a.len}"
							stroke-dashoffset={-a.off}
							opacity={active && active !== a.key ? 0.35 : 1}
							role="img"
							tabindex="0"
							aria-label="{t(`overview.fees.${a.key}`)}: {fees[a.key]} ({pct(fees[a.key])}%)"
							onmouseenter={() => (active = a.key)}
							onmouseleave={() => (active = null)}
							onfocus={() => (active = a.key)}
							onblur={() => (active = null)} />
					{/if}
				{/each}
			</svg>
			<div class="pointer-events-none absolute inset-0 grid place-content-center px-6 text-center">
				<span class="font-display text-2xl leading-none font-extrabold text-ink">{active ? fees[active] : total}</span>
				<span class="mt-0.5 text-[10px] leading-tight text-muted">{active ? `${pct(fees[active])}% ${t(`overview.fees.${active}`).toLowerCase()}` : t('overview.fees.total')}</span>
			</div>
			</div>
			<ul class="grid gap-1.5 text-sm">
				{#each KEYS as k (k)}
					<li class="flex items-center gap-2 rounded px-1 {active === k ? 'bg-surface' : ''}" role="presentation" onmouseenter={() => (active = k)} onmouseleave={() => (active = null)}>
						<span class="h-3 w-3 rounded-sm" style="background: {colour[k]}"></span><span class="text-muted">{t(`overview.fees.${k}`)}</span><span class="ml-auto pl-3 font-mono text-xs font-semibold text-ink">{fees[k]} <span class="font-normal text-muted">· {pct(fees[k])}%</span></span>
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>
