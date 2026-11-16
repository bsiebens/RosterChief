<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { Workload } from '#lib/referees.ts';

	/** Horizontal bars: games per person this season (hand-written SVG), referees in the club colour, officials in ice blue. */
	let { rows }: { rows: Workload[] } = $props();

	const top = $derived(rows.slice(0, 12));
	const maxGames = $derived(Math.max(1, ...top.map((r) => r.games)));
	const ROW = 26;
	const LABEL = 150;
	const W = 560;
</script>

<svg viewBox="0 0 {W} {top.length * ROW + 4}" class="w-full" role="img" aria-label={t('referees.workload.chart')}>
	{#each top as r, i (r.key)}
		{@const w = ((W - LABEL - 36) * r.games) / maxGames}
		<g transform="translate(0 {i * ROW + 2})">
			<text x={LABEL - 8} y="16" text-anchor="end" font-size="12" fill="var(--color-slate)">{r.name.length > 22 ? `${r.name.slice(0, 21)}…` : r.name}</text>
			<rect x={LABEL} y="4" width={w} height="16" rx="2" fill={r.kind === 'referee' ? 'var(--color-club)' : 'var(--color-ice)'} />
			<text x={LABEL + w + 6} y="16" font-size="12" font-family="var(--font-mono)" fill="var(--color-ink)">{r.games}</text>
		</g>
	{/each}
</svg>
