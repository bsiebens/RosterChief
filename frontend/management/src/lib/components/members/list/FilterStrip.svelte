<script lang="ts">
	import { Search, X } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { FEE_STATUSES, STATUSES, hasActiveFilters, pageHref, type ListFilters, type SeasonOption, type TeamOption } from '#lib/members/list.ts';
	import KindTabs from './KindTabs.svelte';

	let {
		filters,
		teams,
		seasons,
		showFees,
		onnavigate,
		ontype
	}: {
		filters: ListFilters;
		teams: TeamOption[];
		seasons: SeasonOption[];
		showFees: boolean;
		/** Navigate to a new page URL (select changes, clear, tabs). */
		onnavigate: (href: string) => void;
		/** Typing in the search box: the page debounces and replaces history. */
		ontype: (q: string) => void;
	} = $props();

	// svelte-ignore state_referenced_locally
	let q = $state(filters.q);
	// Keep the box in sync when the URL changes elsewhere (clear filters, back button).
	$effect(() => {
		if (filters.q !== q.trim()) q = filters.q;
	});
	const pick = (key: keyof ListFilters) => (e: Event) => onnavigate(pageHref(filters, { [key]: (e.currentTarget as HTMLSelectElement).value }));
</script>

<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-7 py-2.5">
	<div class="flex flex-wrap items-center gap-2" role="search">
		<div class="relative">
			<span class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim"><Search size={14} /></span>
			<input type="search" aria-label={t('members.list.search')} placeholder={t('members.list.search_placeholder')} class="input w-56 pl-9" bind:value={q} oninput={() => ontype(q)} />
		</div>
		<select class="select w-auto" aria-label={t('members.list.status')} value={filters.status} onchange={pick('status')}>
			<option value="">{filters.kind === 'member' ? t('members.list.status_open') : t('members.list.status_any')}</option>
			{#if filters.kind === 'member'}<option value="all">{t('members.list.status_any')}</option>{/if}
			{#each STATUSES as s (s)}<option value={s}>{t(`members.list.status.${s}`)}</option>{/each}
		</select>
		{#if showFees}
			<select class="select w-auto" aria-label={t('members.list.fee_status')} value={filters.fee_status} onchange={pick('fee_status')}>
				<option value="">{t('members.list.fee_any')}</option>
				{#each FEE_STATUSES as s (s)}<option value={s}>{t(`members.list.fee.${s}`)}</option>{/each}
			</select>
		{/if}
		<select class="select w-auto" aria-label={t('members.list.team')} value={filters.team} onchange={pick('team')}>
			<option value="">{t('members.list.team_any')}</option>
			<option value="none">{t('members.list.team_none')}</option>
			{#each teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
		</select>
		<select class="select w-auto" aria-label={t('members.list.season')} value={filters.season} onchange={pick('season')}>
			<option value="">{t('members.list.season_current')}</option>
			{#each seasons as s (s.id)}<option value={s.id}>{s.name}{s.is_current ? ` (${t('members.list.current')})` : ''}</option>{/each}
		</select>
		{#if hasActiveFilters(filters)}
			<a
				class="btn btn-outline gap-2"
				href="?"
				onclick={(e) => {
					e.preventDefault();
					onnavigate('?');
				}}><X size={14} /> {t('members.list.clear')}</a>
		{/if}
	</div>

	<div class="flex-1"></div>
	<KindTabs {filters} {onnavigate} />
</div>
