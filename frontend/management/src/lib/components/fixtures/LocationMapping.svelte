<script lang="ts" module>
	export type LocationChoice = { mode: 'none' | 'existing' | 'create'; locationId: string; name: string; address: string; zip_code: string; city: string; country: string };
</script>

<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { Country } from '#lib/calendar.ts';
	import type { LocationRef, UnknownLocation } from '#lib/fixtures.ts';

	/** One venue text of the fixture list without a matching location: no location, an existing one, or a new one. */
	let { item, choice = $bindable(), locations, countries, errors }: { item: UnknownLocation; choice: LocationChoice; locations: LocationRef[]; countries: Country[]; errors: Record<string, string> } = $props();

	const uid = $props.id();
	const suggested = $derived(new Set(item.suggestions.map((s) => s.id)));
	const err = (k?: string) => errors[k ? `locations.${item.source_text}.${k}` : `locations.${item.source_text}`];
	const label = (l: LocationRef) => (l.city && l.city !== l.name ? `${l.name} (${l.city})` : l.name);
</script>

<div class="grid gap-3 border-t border-line px-6 py-4 first:border-t-0 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
	<div>
		<p class="font-semibold break-words text-ink">{item.source_text}</p>
		<p class="text-xs text-muted">{item.game_ids.length === 1 ? t('fixtures.games_one') : t('fixtures.games_n', { n: item.game_ids.length })}</p>
	</div>
	<fieldset class="grid gap-2 text-sm">
		<legend class="sr-only">{t('fixtures.map.location_for', { name: item.source_text })}</legend>
		<label class="flex cursor-pointer items-center gap-2"><input type="radio" class="size-4 accent-club" name="{uid}-mode" value="none" bind:group={choice.mode} /> {t('fixtures.map.no_location')}</label>
		<div class="flex flex-wrap items-center gap-2">
			<label class="flex cursor-pointer items-center gap-2"><input type="radio" class="size-4 accent-club" name="{uid}-mode" value="existing" bind:group={choice.mode} /> {t('fixtures.map.same_location')}</label>
			{#if choice.mode === 'existing'}
				<select class="select select-sm w-full max-w-xs {err() ? 'select-error' : ''}" aria-label={t('fixtures.map.same_location')} bind:value={choice.locationId}>
					<option value="" disabled>{t('fixtures.map.pick')}</option>
					{#if item.suggestions.length}
						<optgroup label={t('fixtures.map.suggestions')}>
							{#each item.suggestions as s (s.id)}<option value={s.id}>{label(s)}</option>{/each}
						</optgroup>
					{/if}
					<optgroup label={t('fixtures.map.all_locations')}>
						{#each locations.filter((l) => !suggested.has(l.id)) as l (l.id)}<option value={l.id}>{label(l)}</option>{/each}
					</optgroup>
				</select>
			{/if}
		</div>
		<label class="flex cursor-pointer items-center gap-2"><input type="radio" class="size-4 accent-club" name="{uid}-mode" value="create" bind:group={choice.mode} /> {t('fixtures.map.create_location')}</label>
		{#if choice.mode === 'create'}
			<div class="grid gap-2 rounded-lg border border-line p-3 sm:grid-cols-2">
				{#each [['name', 'fixtures.loc.name'], ['address', 'fixtures.loc.address'], ['zip_code', 'fixtures.loc.zip'], ['city', 'fixtures.loc.city']] as const as [k, key] (k)}
					<label class="grid gap-1 text-xs text-muted">
						{t(key)}
						<input class="input input-sm w-full {err(k) ? 'input-error' : ''}" bind:value={choice[k]} />
						{#if err(k)}<span class="text-error">{err(k)}</span>{/if}
					</label>
				{/each}
				<label class="grid gap-1 text-xs text-muted">
					{t('fixtures.loc.country')}
					<select class="select select-sm w-full {err('country') ? 'select-error' : ''}" bind:value={choice.country}>
						<option value="">—</option>
						{#each countries as c (c.code)}<option value={c.code}>{c.name}</option>{/each}
					</select>
					{#if err('country')}<span class="text-error">{err('country')}</span>{/if}
				</label>
			</div>
		{/if}
		{#if err()}<p class="text-xs text-error">{err()}</p>{/if}
	</fieldset>
</div>
