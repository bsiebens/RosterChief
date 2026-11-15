<script lang="ts">
	import { ClipboardList } from '@lucide/svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { statusLabelKey, statusStyle, type AttendanceList, type AttendanceRow } from '#lib/events/types.ts';

	/** Phone version of the Attendance tab: compact count tiles, filter chips and one card per player (read-only). */
	let { eventId, list, manage }: { eventId: string; list: AttendanceList; manage: boolean } = $props();

	let filter = $state<'all' | 'present' | 'maybe' | 'absent' | 'no_response'>('all');
	let query = $state('');
	const group = (row: AttendanceRow) => (['present', 'selected', 'not_selected'].includes(row.status) ? 'present' : ['absent', 'excused', 'dropout'].includes(row.status) ? 'absent' : row.status === 'maybe' ? 'maybe' : 'no_response');
	const count = (key: string) => (key === 'all' ? list.rows.length : list.rows.filter((row) => group(row) === key).length);
	const rows = $derived(list.rows.filter((row) => (filter === 'all' || group(row) === filter) && (!query.trim() || `${row.last_name} ${row.first_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))));
	const TILES = ['present', 'maybe', 'absent', 'no_response'] as const;
	const chips = [['all', 'portal.all'], ['present', 'events.status.present'], ['maybe', 'events.status.maybe'], ['absent', 'events.status.absent'], ['no_response', 'events.status.no_response']] as const;
	const sub = (row: AttendanceRow) => [row.jersey_number != null ? `#${row.jersey_number}` : '', row.position].filter(Boolean).join(' · ');
</script>

<div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3">
	<div class="grid grid-cols-3 gap-2">
		{#each TILES as key (key)}
			<div class="card min-w-0 px-2 py-2.5 text-center">
				<span class="block font-display text-2xl leading-none font-extrabold text-ink">{count(key)}</span>
				<span class="mt-1 block truncate text-[11px] font-semibold text-muted">{t(statusLabelKey(key))}</span>
			</div>
		{/each}
		{#if list.checked_in || list.no_shows}
			<div class="card min-w-0 px-2 py-2.5 text-center">
				<span class="block font-display text-2xl leading-none font-extrabold text-ink">{list.checked_in}</span>
				<span class="mt-1 block truncate text-[11px] font-semibold text-muted">{t('events.att.checked_in')}</span>
			</div>
			<div class="card min-w-0 px-2 py-2.5 text-center">
				<span class="block font-display text-2xl leading-none font-extrabold text-ink">{list.no_shows}</span>
				<span class="mt-1 block truncate text-[11px] font-semibold text-muted">{t('events.att.no_shows')}</span>
			</div>
		{/if}
	</div>

	{#if manage}
		<a class="btn btn-primary min-h-12 gap-2" href="/coach/attendance/{eventId}"><ClipboardList size={16} aria-hidden="true" /> {t('coach.ev_manage_att')}</a>
	{/if}

	{#if list.rows.length}
		<section class="card overflow-hidden">
			<div class="flex gap-1 overflow-x-auto border-b border-line px-3 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" role="group" aria-label={t('portal.all')}>
				{#each chips as [key, label] (key)}
					<button type="button" class="portal-person compact text shrink-0 !px-3" aria-pressed={filter === key} onclick={() => (filter = key)}>{t(label)} {count(key)}</button>
				{/each}
			</div>
			<div class="border-b border-line px-3 py-2">
				<label class="sr-only" for="att-q">{t('portal.search_players')}</label>
				<input id="att-q" type="search" class="input w-full" placeholder={t('portal.search_players')} bind:value={query} />
			</div>
			<ul class="divide-y divide-rule">
				{#each rows as row (row.member.id)}
					<li class="flex min-h-14 items-center gap-3 px-3.5 py-2">
						<Avatar first_name={row.first_name} last_name={row.last_name} size="size-9" text="text-xs" />
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-semibold text-ink">{row.last_name}, {row.first_name}</p>
							<p class="truncate text-xs text-muted">{sub(row)}{#if row.showed_up !== null} · {t(row.showed_up ? 'events.att.checked_in' : 'events.att.no_show')}{/if}</p>
						</div>
						<span class="badge badge-sm shrink-0 border {statusStyle(row.status)}">{t(statusLabelKey(row.status))}</span>
					</li>
				{:else}
					<li class="px-4 py-3 text-sm text-muted">{t('portal.no_players')}</li>
				{/each}
			</ul>
		</section>
	{:else}
		<p class="card card-body text-sm text-muted">{t('events.att.none')}</p>
	{/if}
</div>
