<script lang="ts" module>
	export type OpponentChoice = { mode: 'keep' | 'create' | 'existing'; name: string; opponentId: string; filter: string };
</script>

<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import type { Ref, UnknownOpponent } from '#lib/fixtures.ts';

	/** One opponent spelling of the fixture list that matches no opponent yet: create one, or map it onto an existing one. */
	let { item, choice = $bindable(), opponents, error }: { item: UnknownOpponent; choice: OpponentChoice; opponents: Ref[]; error?: string } = $props();

	const uid = $props.id();
	const suggested = $derived(new Set(item.suggestions.map((s) => s.id)));
	const others = $derived.by(() => {
		const q = choice.filter.trim().toLowerCase();
		return opponents.filter((o) => !suggested.has(o.id) && (o.id === choice.opponentId || !q || o.name.toLowerCase().includes(q)));
	});
</script>

<div class="grid gap-3 border-t border-line px-6 py-4 first:border-t-0 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
	<div>
		<p class="font-semibold break-words text-ink">{item.source_name}</p>
		<p class="text-xs text-muted">{item.game_ids.length === 1 ? t('fixtures.games_one') : t('fixtures.games_n', { n: item.game_ids.length })}</p>
	</div>
	<fieldset class="grid gap-2 text-sm">
		<legend class="sr-only">{t('fixtures.map.opponent_for', { name: item.source_name })}</legend>
		{#if item.current}
			<label class="flex cursor-pointer items-center gap-2"><input type="radio" class="size-4 accent-club" name="{uid}-mode" value="keep" bind:group={choice.mode} /> {t('fixtures.map.keep', { name: item.current.name })}</label>
		{/if}
		<div class="flex flex-wrap items-center gap-2">
			<label class="flex cursor-pointer items-center gap-2"><input type="radio" class="size-4 accent-club" name="{uid}-mode" value="create" bind:group={choice.mode} /> {t('fixtures.map.create_opponent')}</label>
			{#if choice.mode === 'create'}
				<input class="input input-sm w-full max-w-xs {error ? 'input-error' : ''}" aria-label={t('fixtures.map.new_name')} bind:value={choice.name} />
			{/if}
		</div>
		<div class="flex flex-wrap items-center gap-2">
			<label class="flex cursor-pointer items-center gap-2"><input type="radio" class="size-4 accent-club" name="{uid}-mode" value="existing" bind:group={choice.mode} /> {t('fixtures.map.same_opponent')}</label>
			{#if choice.mode === 'existing'}
				<input class="input input-sm w-40" type="search" placeholder={t('fixtures.map.search')} aria-label={t('fixtures.map.search')} bind:value={choice.filter} />
				<select class="select select-sm w-full max-w-xs {error ? 'select-error' : ''}" aria-label={t('fixtures.map.same_opponent')} bind:value={choice.opponentId}>
					<option value="" disabled>{t('fixtures.map.pick')}</option>
					{#if item.suggestions.length}
						<optgroup label={t('fixtures.map.suggestions')}>
							{#each item.suggestions as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
						</optgroup>
					{/if}
					<optgroup label={t('fixtures.map.all_opponents')}>
						{#each others as o (o.id)}<option value={o.id}>{o.name}</option>{/each}
					</optgroup>
				</select>
			{/if}
		</div>
		{#if choice.mode === 'existing'}<p class="text-xs text-muted">{t('fixtures.map.alias_note', { name: item.source_name })}</p>{/if}
		{#if error}<p class="text-xs text-error">{error}</p>{/if}
	</fieldset>
</div>
