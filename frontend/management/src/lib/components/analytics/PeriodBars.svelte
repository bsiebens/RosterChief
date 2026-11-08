<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { ForAgainst } from '#lib/analytics.ts';

	/** Goals per period as mirrored bars: for (ink) grows left from the period label, against (club colour) right. */
	let { rows, label, tipLabel }: { rows: ForAgainst[]; label: (row: ForAgainst) => string; tipLabel: (row: ForAgainst) => string } = $props();
	const max = $derived(Math.max(1, ...rows.map((r) => Math.max(r.goals_for, r.goals_against))));
	let hover = $state<number | null>(null);
</script>

<div role="presentation" class="grid gap-3 pt-2" onmouseleave={() => (hover = null)}>
	{#each rows as row, i (row.label)}
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div
			class="grid grid-cols-[1.75rem_minmax(0,1fr)_2.5rem_minmax(0,1fr)_1.75rem] items-center gap-2 rounded-lg px-1 py-0.5 transition-colors {hover === i ? 'bg-paper' : ''}"
			role="img"
			tabindex="0"
			aria-label="{tipLabel(row)}: {row.goals_for} {t('analytics.goals_for')}, {row.goals_against} {t('analytics.goals_against')}"
			title="{tipLabel(row)}: {row.goals_for} – {row.goals_against}"
			onmouseenter={() => (hover = i)}
			onfocus={() => (hover = i)}
			onblur={() => (hover = null)}>
			<span class="text-right font-display text-base font-extrabold text-ink">{row.goals_for}</span>
			<div class="flex justify-end"><div class="h-5 min-w-[3px] rounded-md bg-ink" style="width: {(row.goals_for / max) * 100}%"></div></div>
			<span class="text-center font-display text-sm font-extrabold text-ink">{label(row)}</span>
			<div class="flex justify-start"><div class="h-5 min-w-[3px] rounded-md bg-club" style="width: {(row.goals_against / max) * 100}%"></div></div>
			<span class="text-right font-display text-base font-extrabold text-club-dark">{row.goals_against}</span>
		</div>
	{/each}
</div>
