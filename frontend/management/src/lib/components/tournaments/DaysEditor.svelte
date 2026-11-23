<script lang="ts">
	import { Plus, X } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { TDay } from '#lib/tournaments.svelte.ts';

	let { days = $bindable(), error = '' }: { days: TDay[]; error?: string } = $props();
	const id = $props.id();

	/** The next day after the last one, with the same hours, so a weekend is two clicks. */
	function add() {
		const last = days[days.length - 1];
		let date = '';
		if (last?.date) {
			const d = new Date(`${last.date}T12:00:00`);
			d.setDate(d.getDate() + 1);
			date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
		}
		days = [...days, { date, starts_at: last?.starts_at ?? '09:00', ends_at: last?.ends_at ?? '17:00' }];
	}
	const remove = (i: number) => (days = days.filter((_, j) => j !== i));
</script>

<div class="grid gap-3">
	<p class="text-xs text-muted">{t('tn.days.help')}</p>
	<div class="grid gap-2">
		<div class="hidden grid-cols-[1fr_8rem_8rem_2.25rem] gap-2 px-1 font-display text-xs font-bold tracking-[.08em] text-muted uppercase sm:grid">
			<span>{t('tn.days.date')}</span><span>{t('tn.days.starts')}</span><span>{t('tn.days.ends')}</span><span></span>
		</div>
		{#each days as day, i (i)}
			<div class="grid grid-cols-[1fr_1fr_2.25rem] items-center gap-2 sm:grid-cols-[1fr_8rem_8rem_2.25rem]">
				<input id="{id}-date-{i}" class="input input-bordered col-span-3 w-full sm:col-span-1" type="date" aria-label={t('tn.days.date')} bind:value={day.date} required />
				<input class="input input-bordered w-full" type="time" aria-label={t('tn.days.starts')} bind:value={day.starts_at} required />
				<input class="input input-bordered w-full" type="time" aria-label={t('tn.days.ends')} bind:value={day.ends_at} required />
				<button class="btn btn-ghost btn-sm btn-square" type="button" disabled={days.length === 1} aria-label={t('tn.days.remove')} title={t('tn.days.remove')} onclick={() => remove(i)}><X size={16} /></button>
			</div>
		{/each}
	</div>
	{#if error}<span class="text-xs text-error">{error}</span>{/if}
	<div><button class="btn btn-outline btn-sm gap-2" type="button" onclick={add}><Plus size={14} /> {t('tn.days.add')}</button></div>
</div>
