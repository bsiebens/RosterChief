<script lang="ts">
	import { ChevronRight, Repeat as RepeatIcon } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { describeRecurrence, REPEATS, weekdayName, weekdayOf, WEEKDAY_CODES, type FormState, type Repeat } from '#lib/events/form-types.ts';

	/**
	 * "Repeat" block: frequency, interval, weekdays (weekly), end (never / on a date / after N times), a summary line and
	 * an optional raw RRULE. `allowNone` false when editing a series (it cannot become a single event). `current` is
	 * the API's description of the saved rule (edit only).
	 */
	let {
		s = $bindable(),
		fields = {},
		allowNone = true,
		current = '',
		note = ''
	}: { s: FormState; fields?: Record<string, string>; allowNone?: boolean; current?: string; note?: string } = $props();

	const uid = $props.id();
	let advanced = $state(false);
	$effect.pre(() => {
		if (s.rrule && !advanced) advanced = true;
	});
	const summary = $derived(describeRecurrence(s));
	const unit = $derived(
		s.repeat === 'daily' ? t('eventform.rec.unit.days') : s.repeat === 'weekly' ? t('eventform.rec.unit.weeks') : t('eventform.rec.unit.months')
	);
	const rawRule = $derived(!!s.rrule.trim());

	function setRepeat(r: Repeat) {
		s.repeat = r;
		// Weekly starts on the start date's weekday.
		if (r === 'weekly' && !s.weekdays.length) {
			const wd = weekdayOf(s.start);
			if (wd) s.weekdays = [wd];
		}
	}
	function toggleDay(code: string) {
		s.weekdays = s.weekdays.includes(code) ? s.weekdays.filter((c) => c !== code) : [...s.weekdays, code];
	}
	const err = (k: string) => fields[k];
</script>

<div class="grid gap-3">
	<div class="grid gap-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)] sm:items-end">
		<div class="form-control w-full">
			<label class="label" for="{uid}-repeat"><span class="label-text">{t('eventform.rec.repeat')}</span></label>
			<select id="{uid}-repeat" class="select select-bordered w-full {err('frequency') ? 'select-error' : ''}" value={s.repeat} disabled={rawRule} onchange={(e) => setRepeat(e.currentTarget.value as Repeat)}>
				{#each REPEATS.filter((r) => allowNone || r !== 'none') as r (r)}<option value={r}>{t(`eventform.rec.freq.${r}`)}</option>{/each}
			</select>
			{#if err('frequency')}<span class="mt-1 block text-xs text-error">{err('frequency')}</span>{/if}
		</div>
		{#if s.repeat !== 'none'}
			<div class="form-control">
				<label class="label" for="{uid}-interval"><span class="label-text">{t('eventform.rec.every')}</span></label>
				<div class="flex items-center gap-2">
					<input id="{uid}-interval" class="input input-bordered w-20 {err('interval') ? 'input-error' : ''}" type="number" min="1" max="99" bind:value={s.interval} disabled={rawRule} />
					<span class="text-sm text-slate">{unit}</span>
				</div>
				{#if err('interval')}<span class="mt-1 block text-xs text-error">{err('interval')}</span>{/if}
			</div>
		{/if}
	</div>

	{#if s.repeat === 'weekly'}
		<fieldset class="grid gap-1.5" disabled={rawRule}>
			<legend class="label-text mb-1.5">{t('eventform.rec.on_days')}</legend>
			<div class="flex flex-wrap gap-1.5">
				{#each WEEKDAY_CODES as code (code)}
					{@const on = s.weekdays.includes(code)}
					<button
						type="button"
						class="h-9 min-w-11 rounded-full border px-3 font-display text-sm font-bold tracking-wide uppercase transition-colors {on ? 'border-primary bg-primary text-primary-content' : 'border-stroke bg-white text-slate hover:border-slate'}"
						aria-pressed={on}
						aria-label={weekdayName(code)}
						onclick={() => toggleDay(code)}>{weekdayName(code, 'short')}</button>
				{/each}
			</div>
			{#if err('weekdays')}<span class="block text-xs text-error">{err('weekdays')}</span>{/if}
		</fieldset>
	{/if}

	{#if s.repeat !== 'none'}
		<fieldset class="grid gap-2" disabled={rawRule}>
			<legend class="label-text mb-1">{t('eventform.rec.ends')}</legend>
			<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="radio" class="size-4 accent-club" name="{uid}-ends" value="never" bind:group={s.ends} /> {t('eventform.rec.ends.never')}</label>
			<div class="flex flex-wrap items-center gap-2 text-sm">
				<label class="flex cursor-pointer items-center gap-2"><input type="radio" class="size-4 accent-club" name="{uid}-ends" value="until" bind:group={s.ends} /> {t('eventform.rec.ends.until')}</label>
				<input class="input input-bordered input-sm w-44 {err('until') ? 'input-error' : ''}" type="date" aria-label={t('eventform.rec.ends.until_date')} bind:value={s.until} min={s.start.slice(0, 10) || undefined} disabled={s.ends !== 'until'} />
			</div>
			<div class="flex flex-wrap items-center gap-2 text-sm">
				<label class="flex cursor-pointer items-center gap-2"><input type="radio" class="size-4 accent-club" name="{uid}-ends" value="count" bind:group={s.ends} /> {t('eventform.rec.ends.after')}</label>
				<input class="input input-bordered input-sm w-20 {err('count') ? 'input-error' : ''}" type="number" min="1" max="1000" aria-label={t('eventform.rec.ends.count')} bind:value={s.count} disabled={s.ends !== 'count'} />
				<span>{t('eventform.rec.ends.times')}</span>
			</div>
			{#if err('until')}<span class="block text-xs text-error">{err('until')}</span>{/if}
			{#if err('count')}<span class="block text-xs text-error">{err('count')}</span>{/if}
		</fieldset>
	{/if}

	{#if summary}
		<p class="flex items-start gap-2 rounded-lg border border-info-border bg-info-bg px-3 py-2 text-sm text-info-text" aria-live="polite">
			<RepeatIcon size={15} class="mt-0.5 shrink-0" aria-hidden="true" />
			<span>{summary}</span>
		</p>
	{/if}
	{#if current}<p class="text-xs text-muted">{t('eventform.rec.current', { text: current })}</p>{/if}
	{#if note}<p class="text-xs text-muted">{note}</p>{/if}
	{#if err('recurrence')}<span class="block text-xs text-error">{err('recurrence')}</span>{/if}

	{#if s.repeat !== 'none' || s.rrule}
		<div>
			<button type="button" class="inline-flex items-center gap-1 text-xs font-semibold text-muted hover:text-ink" aria-expanded={advanced} aria-controls="{uid}-adv" onclick={() => (advanced = !advanced)}>
				<ChevronRight size={14} class="transition-transform {advanced ? 'rotate-90' : ''}" aria-hidden="true" />
				{t('eventform.rec.advanced')}
			</button>
			{#if advanced}
				<div id="{uid}-adv" class="mt-2 grid gap-1">
					<label class="label-text" for="{uid}-rrule">{t('eventform.rec.rrule')}</label>
					<input id="{uid}-rrule" class="input input-bordered w-full font-mono text-sm {err('rrule') ? 'input-error' : ''}" placeholder="FREQ=WEEKLY;BYDAY=MO,WE" autocomplete="off" spellcheck="false" bind:value={s.rrule} />
					{#if err('rrule')}<span class="block text-xs text-error">{err('rrule')}</span>{:else}<span class="text-xs text-muted">{t('eventform.rec.rrule_help')}</span>{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>
