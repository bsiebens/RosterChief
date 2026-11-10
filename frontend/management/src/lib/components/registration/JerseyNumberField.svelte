<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { fetchNumbers, type NumbersInfo } from '#lib/registration-public.ts';

	/**
	 * The jersey number of a person registering as a player. For a team with a number pool it is a select of the numbers that are still free
	 * (the pool's range, minus reservations and the numbers held by players who may not share them, which depends on the date of birth, minus the
	 * numbers the other people in this form picked on the same team). For a team without a pool it is a number box checked against the numbers its roster
	 * holds. The server checks again on submit. The list is fetched when the team, date of birth, option or `exclude` change (debounced, stale answers dropped).
	 */
	let { id, teamId, dob = '', variantId = '', exclude = [], value = $bindable(''), error = '', refresh = 0 }: { id: string; teamId: string; dob?: string; variantId?: string; exclude?: number[]; value?: string; error?: string; refresh?: number } = $props();

	let info = $state<NumbersInfo | null>(null);
	let loading = $state(false);
	let failed = $state(false);
	let cleared = $state<number | null>(null);
	let lastTeam = '';
	const key = $derived(JSON.stringify([teamId, dob, variantId, exclude, refresh])); // `refresh` is bumped when the server refused a number (someone else just took it)

	$effect(() => {
		key; // re-run when anything the list depends on changes
		if (!teamId) {
			info = null;
			loading = false;
			failed = false;
			return;
		}
		if (teamId !== lastTeam) {
			// another team: the old list is wrong, show the loading state instead of it
			info = null;
			lastTeam = teamId;
		}
		loading = true;
		const controller = new AbortController();
		const timer = setTimeout(async () => {
			const res = await fetchNumbers({ team_id: teamId, date_of_birth: dob || undefined, option_id: variantId || undefined, exclude: exclude.join(',') || undefined }, controller.signal);
			if (controller.signal.aborted) return;
			loading = false;
			failed = !res.ok;
			info = res.ok ? (res.data ?? null) : null;
			const picked = value.trim() === '' ? null : Number(value);
			if (info?.numbers && picked !== null && !info.numbers.includes(picked)) {
				cleared = picked;
				value = '';
			}
		}, 300);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});
	$effect(() => {
		if (value !== '') cleared = null;
	});

	const taken = $derived(info && !info.numbers && value.trim() !== '' && info.taken.includes(Number(value)));
	const problem = $derived(error || (taken ? t('join.num.taken', { n: value }) : ''));
</script>

<div class="form-control">
	<label class="label" for={id}><span class="label-text">{t('reg.f.jersey')}</span></label>
	{#if !teamId}
		<select {id} class="select select-bordered w-full" disabled aria-describedby="{id}-hint"><option value="">{t('join.num.pick_team')}</option></select>
	{:else if loading && !info}
		<select {id} class="select select-bordered w-full" disabled aria-busy="true"><option value="">{t('join.num.loading')}</option></select>
	{:else if info?.numbers}
		<select {id} class="select select-bordered w-full {problem ? 'select-error' : ''}" bind:value aria-invalid={problem ? 'true' : undefined} aria-describedby="{id}-hint" disabled={info.numbers.length === 0}>
			<option value="">{t('reg.f.no_preference')}</option>
			{#each info.numbers as n (n)}<option value={String(n)}>{n}</option>{/each}
		</select>
	{:else}
		<input {id} type="number" inputmode="numeric" min="0" max="999" class="input input-bordered w-full {problem ? 'input-error' : ''}" bind:value aria-invalid={problem ? 'true' : undefined} aria-describedby="{id}-hint" />
	{/if}
	<div id="{id}-hint" aria-live="polite">
		{#if problem}
			<span class="mt-1 block text-xs text-error">{problem}</span>
		{:else if teamId && info?.numbers && info.numbers.length === 0}
			<span class="mt-1 block text-xs text-error" role="status">{t('join.num.empty')}</span>
		{:else if cleared !== null}
			<span class="mt-1 block text-xs text-warn-text">{t('join.num.cleared', { n: cleared })}</span>
		{:else if failed}
			<span class="mt-1 block text-xs text-muted">{t('join.num.error')}</span>
		{:else if info?.pool}
			<span class="label-text-alt mt-1 block">{t('join.num.range', { min: info.pool.min, max: info.pool.max, pool: info.pool.name })}</span>
		{:else if teamId && info}
			<span class="label-text-alt mt-1 block">{t('join.num.free_hint')}</span>
		{/if}
	</div>
</div>
