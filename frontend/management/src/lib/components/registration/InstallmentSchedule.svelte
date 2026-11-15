<script lang="ts">
	import { CalendarClock } from '@lucide/svelte';
	import { formatDay, parseIsoDate } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Installment } from '#lib/registrations.ts';
	import { money } from '#lib/shop.ts';

	let { rows }: { rows: Installment[] } = $props();
	const heading = $props.id();
</script>

<section class="card card-body gap-3" aria-labelledby={heading}>
	<h2 id={heading} class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">
		<CalendarClock size={16} aria-hidden="true" /> {t('installments.schedule')}
	</h2>
	<div class="overflow-x-auto">
		<table class="table">
			<thead><tr><th>{t('fin.col.due_date')}</th><th class="text-right">{t('fin.col.amount')}</th><th class="text-right">{t('installments.remaining')}</th></tr></thead>
			<tbody>
				{#each rows as row (row.due_date)}
					<tr>
						<td class="whitespace-nowrap">{formatDay(parseIsoDate(row.due_date) ?? row.due_date)} {#if row.late}<span class="badge badge-error badge-sm">{t('installments.late')}</span>{/if}</td>
						<td class="text-right font-mono">{money(row.amount)}</td>
						<td class="text-right font-mono font-semibold">{money(row.remaining)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>
