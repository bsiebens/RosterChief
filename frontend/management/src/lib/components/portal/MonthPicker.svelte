<script lang="ts">
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';
	import { formatMonth } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';

	let { month = $bindable(), selected = $bindable(), events }: { month: Date; selected: string; events: Set<string> } = $props();
	const dayKey = (date: Date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
	const days = $derived.by(() => {
		const year = month.getFullYear(), index = month.getMonth();
		const offset = (new Date(year, index, 1).getDay() + 6) % 7;
		const total = Math.ceil((offset + new Date(year, index + 1, 0).getDate()) / 7) * 7;
		return Array.from({ length: total }, (_, day) => new Date(year, index, day - offset + 1));
	});
	const weekday = (i: number) => new Intl.DateTimeFormat(getLang(), { weekday: 'short' }).format(new Date(2024, 0, i + 1));
	function move(step: number) {
		month = new Date(month.getFullYear(), month.getMonth() + step, 1);
		selected = '';
	}
</script>

<section class="card card-body">
	<div class="mb-1 flex items-center justify-between">
		<button type="button" class="btn btn-ghost btn-square" aria-label={t('portal.month_previous')} onclick={() => move(-1)}><ChevronLeft size={18} aria-hidden="true" /></button>
		<h2 class="card-title">{formatMonth(month)}</h2>
		<button type="button" class="btn btn-ghost btn-square" aria-label={t('portal.month_next')} onclick={() => move(1)}><ChevronRight size={18} aria-hidden="true" /></button>
	</div>
	<div class="grid grid-cols-7 gap-1 text-center">
		{#each Array(7) as _, i (i)}<span class="text-[11px] font-semibold text-muted">{weekday(i)}</span>{/each}
		{#each days as date (dayKey(date))}
			{@const key = dayKey(date)}
			<button type="button" class="relative grid aspect-square max-h-12 min-h-8 place-items-center rounded-box text-sm tabular-nums {selected === key ? 'bg-club font-bold text-club-content' : 'hover:bg-subhead'} {date.getMonth() === month.getMonth() ? '' : 'text-dim'}"
				aria-label={new Intl.DateTimeFormat(getLang(), { dateStyle: 'full' }).format(date)}
				aria-pressed={selected === key} onclick={() => { selected = selected === key ? '' : key; if (date.getMonth() !== month.getMonth()) month = new Date(date.getFullYear(), date.getMonth(), 1); }}>
				{date.getDate()}
				{#if events.has(key)}<span class="absolute bottom-1 size-1 rounded-full {selected === key ? 'bg-current' : 'bg-club'}"></span>{/if}
			</button>
		{/each}
	</div>
</section>
