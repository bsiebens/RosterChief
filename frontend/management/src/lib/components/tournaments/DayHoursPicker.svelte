<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { fmtDay, type TDay } from '#lib/tournaments.svelte.ts';

	/**
	 * Picks when something happens by clicking: the tournament days as chips (one day, or several with `multi`), and the hours as time
	 * fields with a button that fills in the opening hours of the chosen day. The values are plain `YYYY-MM-DD` and `HH:MM` strings.
	 */
	let {
		days,
		dates = $bindable([]),
		from = $bindable(''),
		to = $bindable(''),
		multi = false,
		error = ''
	}: { days: TDay[]; dates: string[]; from: string; to: string; multi?: boolean; error?: string } = $props();

	/** A day's opening hours arrive as a time ("09:00:00") or as a timestamp. */
	const hhmm = (v: string) => {
		if (/^\d{2}:\d{2}/.test(v)) return v.slice(0, 5);
		const d = new Date(v);
		return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
	};
	/** The tournament days, plus a date that is already chosen but is no tournament day (an old task), so it stays visible. */
	const shown = $derived([...days, ...dates.filter((d) => !days.some((x) => x.date === d)).map((date) => ({ date, starts_at: '', ends_at: '' }))]);
	const first = $derived(shown.find((d) => dates.includes(d.date)));

	function pick(date: string) {
		if (!multi) dates = [date];
		else dates = dates.includes(date) ? dates.filter((d) => d !== date) : [...dates, date].sort();
		if (!from && !to) fill(date);
	}
	/** The opening hours of a tournament day into the time fields. */
	function fill(date: string | undefined = first?.date) {
		const day = days.find((d) => d.date === date);
		if (!day?.starts_at) return;
		from = hhmm(day.starts_at);
		to = hhmm(day.ends_at);
	}
	const allSelected = $derived(days.length > 0 && days.every((d) => dates.includes(d.date)));
</script>

<fieldset class="grid gap-3">
	<div class="grid gap-1.5">
		<legend class="label-text text-sm font-medium">{multi ? t('tn.tasks.f.days') : t('tn.tasks.f.day')}</legend>
		<div class="flex flex-wrap gap-1.5">
			{#each shown as d (d.date)}
				<button type="button" aria-pressed={dates.includes(d.date)} onclick={() => pick(d.date)} class="rounded-lg border-2 px-3 py-1.5 text-sm font-semibold {dates.includes(d.date) ? 'border-club bg-club/10 text-ink' : 'border-line bg-white text-muted hover:text-ink'}">{fmtDay(d.date)}</button>
			{/each}
			{#if multi && days.length > 1}
				<button type="button" aria-pressed={allSelected} onclick={() => (dates = allSelected ? [] : days.map((d) => d.date))} class="rounded-lg border-2 border-dashed px-3 py-1.5 text-sm font-semibold {allSelected ? 'border-club bg-club/10 text-ink' : 'border-line text-muted hover:text-ink'}">{t('tn.tasks.all_days')}</button>
			{/if}
		</div>
	</div>
	<div class="grid grid-cols-2 gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
		<div class="form-control"><label class="label-text mb-1 text-sm font-medium" for="dh-from">{t('tn.tasks.f.from')}</label><input id="dh-from" class="input input-bordered w-full font-mono" type="time" step="900" bind:value={from} required /></div>
		<div class="form-control"><label class="label-text mb-1 text-sm font-medium" for="dh-to">{t('tn.tasks.f.to')}</label><input id="dh-to" class="input input-bordered w-full font-mono" type="time" step="900" bind:value={to} required /></div>
		<button type="button" class="btn btn-outline col-span-2 sm:col-span-1" disabled={!first?.starts_at} onclick={() => fill()}>{first?.starts_at ? t('tn.tasks.whole_day', { from: hhmm(first.starts_at), to: hhmm(first.ends_at) }) : t('tn.tasks.whole_day_none')}</button>
	</div>
	{#if error}<span class="text-xs text-error">{error}</span>{/if}
</fieldset>
