<script lang="ts">
	import { CircleAlert, Plus, Trash2 } from '@lucide/svelte';
	import { HOCKEY_POSITIONS, normalisePosition } from '#lib/hockey.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { money } from '#lib/shop.ts';
	import { blankPlayer, dayLabel, outOfRange, type LunchMode, type PlayerDraft } from '#lib/tournaments-public.ts';

	/**
	 * The dynamic list of players and staff of a team: names, jersey number, date of birth (players only), licence, dietary needs and, when
	 * lunch is optional, the days the person eats. Ages outside the tournament's range are flagged but never block (staff decide).
	 * `errors` holds server messages keyed `players.<row>.<field>` (the index counts every row, blank ones included).
	 */
	let {
		players = $bindable(),
		tournament,
		errors = {}
	}: {
		players: PlayerDraft[];
		tournament: { ice_hockey?: boolean; dates: string[]; start_date: string; lunch_mode: LunchMode; lunch_price_per_day: string | null; earliest_birth_year: number | null; latest_birth_year: number | null };
		errors?: Record<string, string>;
	} = $props();

	const err = (i: number, field: string) => errors[`players.${i}.${field}`] ?? '';
	const mandatory = $derived(tournament.lunch_mode === 'mandatory');
	const optional = $derived(tournament.lunch_mode === 'optional');
	const hasRange = $derived(tournament.earliest_birth_year !== null || tournament.latest_birth_year !== null);
	const flagged = (p: PlayerDraft) => p.role === 'player' && !!p.date_of_birth && outOfRange(p.date_of_birth, tournament.earliest_birth_year, tournament.latest_birth_year);

	const remove = (uid: number) => (players = players.filter((p) => p.uid !== uid));
	function toggleDay(p: PlayerDraft, day: string, on: boolean) {
		p.lunch_days = on ? [...p.lunch_days, day].sort() : p.lunch_days.filter((d) => d !== day);
	}
	const allDays = (p: PlayerDraft, on: boolean) => (p.lunch_days = on ? [...tournament.dates] : []);
</script>

<div class="grid gap-3">
	{#if hasRange}
		<p class="text-xs text-muted">
			{#if tournament.earliest_birth_year !== null && tournament.latest_birth_year !== null}{t('tp.born.range', { earliest: tournament.earliest_birth_year, latest: tournament.latest_birth_year })}
			{:else if tournament.earliest_birth_year !== null}{t('tp.born.from', { earliest: tournament.earliest_birth_year })}
			{:else}{t('tp.born.until', { latest: tournament.latest_birth_year ?? '' })}{/if}
			{t('tp.age.note')}
		</p>
	{/if}
	{#if mandatory}<p class="text-xs text-muted">{t('tp.lunch.mandatory_note', { price: money(tournament.lunch_price_per_day ?? 0), days: tournament.dates.length })}</p>{/if}

	{#each players as p, i (p.uid)}
		{@const fid = `p${p.uid}`}
		<fieldset class="rounded-box border border-line p-3">
			<legend class="px-1 font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{p.role === 'staff' ? t('tp.role.staff') : t('tp.role.player')} {i + 1}</legend>
			<div class="grid gap-3 sm:grid-cols-6">
				<div class="form-control sm:col-span-3">
					<label class="label" for="{fid}-first"><span class="label-text">{t('tp.f.first_name')}</span></label>
					<input id="{fid}-first" class="input input-bordered w-full {err(i, 'first_name') ? 'input-error' : ''}" bind:value={p.first_name} autocomplete="off" aria-invalid={err(i, 'first_name') ? 'true' : undefined} />
					{#if err(i, 'first_name')}<span class="mt-1 block text-xs text-error">{err(i, 'first_name')}</span>{/if}
				</div>
				<div class="form-control sm:col-span-3">
					<label class="label" for="{fid}-last"><span class="label-text">{t('tp.f.last_name')}</span></label>
					<input id="{fid}-last" class="input input-bordered w-full {err(i, 'last_name') ? 'input-error' : ''}" bind:value={p.last_name} autocomplete="off" aria-invalid={err(i, 'last_name') ? 'true' : undefined} />
					{#if err(i, 'last_name')}<span class="mt-1 block text-xs text-error">{err(i, 'last_name')}</span>{/if}
				</div>
				{#if p.role === 'player'}
					<div class="form-control sm:col-span-2">
						<label class="label" for="{fid}-dob"><span class="label-text">{t('tp.f.dob')}</span></label>
						<input id="{fid}-dob" type="date" class="input input-bordered w-full {err(i, 'date_of_birth') ? 'input-error' : ''}" bind:value={p.date_of_birth} min="1900-01-01" max={new Date().toISOString().slice(0, 10)} aria-invalid={err(i, 'date_of_birth') ? 'true' : undefined} />
						{#if err(i, 'date_of_birth')}<span class="mt-1 block text-xs text-error">{err(i, 'date_of_birth')}</span>{/if}
					</div>
				{/if}
				<div class="form-control {p.role === 'player' ? 'sm:col-span-2' : 'sm:col-span-3'}">
					<label class="label" for="{fid}-pos"><span class="label-text">{p.role === 'staff' ? t('tp.f.function') : t('tp.f.position')}</span></label>
					{#if tournament.ice_hockey && p.role === 'player'}
						<select id="{fid}-pos" class="select select-bordered w-full" value={normalisePosition(p.position_label)} onchange={(e) => (p.position_label = e.currentTarget.value)}>
							<option value="">—</option>
							{#each HOCKEY_POSITIONS as pos (pos.value)}<option value={pos.value}>{t(pos.key)}</option>{/each}
						</select>
					{:else}
						<input id="{fid}-pos" class="input input-bordered w-full" bind:value={p.position_label} />
					{/if}
					{#if err(i, 'position_label')}<span class="mt-1 block text-xs text-error">{err(i, 'position_label')}</span>{/if}
				</div>
				<div class="form-control {p.role === 'player' ? 'sm:col-span-2' : 'sm:col-span-3'}">
					<label class="label" for="{fid}-num"><span class="label-text">{t('tp.f.jersey')}</span></label>
					<input id="{fid}-num" type="number" inputmode="numeric" min="0" max="999" class="input input-bordered w-full {err(i, 'jersey_number') ? 'input-error' : ''}" bind:value={p.jersey_number} aria-invalid={err(i, 'jersey_number') ? 'true' : undefined} />
					{#if err(i, 'jersey_number')}<span class="mt-1 block text-xs text-error">{err(i, 'jersey_number')}</span>{/if}
				</div>
				<div class="form-control sm:col-span-3">
					<label class="label" for="{fid}-lic"><span class="label-text">{t('tp.f.license')}</span></label>
					<input id="{fid}-lic" class="input input-bordered w-full" bind:value={p.license_number} />
				</div>
				<div class="form-control sm:col-span-3">
					<label class="label" for="{fid}-diet"><span class="label-text">{t('tp.f.dietary')}</span></label>
					<input id="{fid}-diet" class="input input-bordered w-full" bind:value={p.dietary_restrictions} placeholder={t('tp.f.dietary_ph')} />
				</div>
			</div>

			{#if optional}
				<div class="mt-3">
					<div class="flex flex-wrap items-center gap-x-3">
						<span class="label-text">{t('tp.f.lunch_days')}{#if tournament.lunch_price_per_day} <span class="text-muted">({t('tp.lunch.per_day', { price: money(tournament.lunch_price_per_day) })})</span>{/if}</span>
						<button type="button" class="link text-xs" onclick={() => allDays(p, p.lunch_days.length < tournament.dates.length)}>{p.lunch_days.length < tournament.dates.length ? t('tp.lunch.all') : t('tp.lunch.none')}</button>
					</div>
					<div class="mt-1 flex flex-wrap gap-x-5 gap-y-1 text-sm">
						{#each tournament.dates as day (day)}
							<label class="flex items-center gap-2"><input type="checkbox" class="checkbox checkbox-sm" checked={p.lunch_days.includes(day)} onchange={(e) => toggleDay(p, day, e.currentTarget.checked)} /> {dayLabel(day, 'short')}</label>
						{/each}
					</div>
				</div>
			{/if}

			{#if flagged(p)}<p class="mt-2 flex items-center gap-2 text-xs text-warn-text" role="status"><CircleAlert size={14} aria-hidden="true" /> {t('tp.age.flag')}</p>{/if}
			<div class="mt-3 flex justify-end">
				<button type="button" class="btn btn-outline btn-error btn-xs gap-1" onclick={() => remove(p.uid)} aria-label={t('tp.remove_n', { n: i + 1 })}><Trash2 size={13} aria-hidden="true" /> {t('tp.remove')}</button>
			</div>
		</fieldset>
	{/each}

</div>
