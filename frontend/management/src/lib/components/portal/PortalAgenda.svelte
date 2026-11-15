<script lang="ts">
	import { MapPin } from '@lucide/svelte';
	import { formatLongDay, formatTime, kindLabelKey, kindStyle } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { CalendarEntry } from '#lib/portal.ts';
	import type { Snippet } from 'svelte';

	/**
	 * The list view of the member calendar, laid out like the manage app's agenda: a sticky header, a heading row per day (today marked), then
	 * one row per event with time, title, location and the family's answer. It opens scrolled to today; the past stays above.
	 */
	let { entries, href, replies, score }: { entries: CalendarEntry[]; href: (eventId: string) => string; replies: Snippet<[CalendarEntry]>; score: Snippet<[CalendarEntry]> } = $props();

	const key = (date: Date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
	const todayKey = key(new Date());
	const groups = $derived.by(() => {
		const map = new Map<string, { key: string; date: Date; entries: CalendarEntry[] }>();
		for (const entry of entries) {
			const date = new Date(entry.event.start);
			const k = key(date);
			if (!map.has(k)) map.set(k, { key: k, date, entries: [] });
			map.get(k)?.entries.push(entry);
		}
		return [...map.values()].sort((a, b) => a.key.localeCompare(b.key));
	});
	const focus = $derived(groups.find((group) => group.key >= todayKey)?.key ?? '');
	function scrollToToday(node: HTMLElement) {
		const target = node.querySelector<HTMLElement>('[data-today]');
		const head = node.querySelector('[data-head]')?.getBoundingClientRect().height ?? 0;
		if (target) node.scrollTop = target.getBoundingClientRect().top - node.getBoundingClientRect().top + node.scrollTop - head;
	}
	const cols = 'lg:grid-cols-[5rem_minmax(0,1.2fr)_minmax(0,0.9fr)_minmax(0,24rem)]';
</script>

<div class="card max-h-[calc(100dvh-13rem)] overflow-auto" use:scrollToToday>
	<div data-head class="sticky top-0 z-10 hidden gap-x-4 border-b border-line bg-subhead px-4 py-2 font-mono text-[11px] tracking-[.1em] text-muted uppercase lg:grid {cols}">
		<span>{t('portal.col_time')}</span><span>{t('portal.col_event')}</span><span>{t('portal.col_location')}</span><span>{t('portal.col_reply')}</span>
	</div>
	{#each groups as group (group.key)}
		<div data-today={group.key === focus ? '' : undefined}>
			<h3 class="flex items-center gap-2 bg-paper px-4 py-2 font-display text-sm font-extrabold tracking-[.04em] uppercase {group.key === todayKey ? 'text-club-dark' : 'text-ink'}">
				{formatLongDay(group.date)}{#if group.key === todayKey}<span class="badge badge-xs border border-club bg-club text-club-content">{t('portal.go_today')}</span>{/if}
			</h3>
			<div class="divide-y divide-rule">
				{#each group.entries as entry (entry.event.event_id)}
					{@const event = entry.event}
					<div class="relative grid items-center gap-x-4 gap-y-1 border-l-4 py-2 pr-4 pl-3 hover:bg-subhead {cols} {kindStyle(event.kind).bar} {event.cancelled ? 'opacity-60' : ''}">
						<span class="font-mono text-xs whitespace-nowrap text-muted">{formatTime(event.start)}{#if event.end} – {formatTime(event.end)}{/if}</span>
						<div class="flex min-w-0 items-center gap-3">
							<a class="min-w-0 flex-1 after:absolute after:inset-0 hover:text-brand focus:outline-none" href={href(event.event_id)} data-sveltekit-reset="false">
								<span class="block truncate font-semibold text-ink {event.cancelled ? 'line-through' : ''}">{event.title}</span>
								<span class="mt-0.5 flex flex-wrap items-center gap-1.5"><span class="badge badge-xs border {kindStyle(event.kind).chip}">{t(kindLabelKey(event.kind))}</span>{#if event.cancelled}<span class="badge badge-xs badge-error">{t('portal.cancelled')}</span>{/if}</span>
							</a>
							{@render score(entry)}
						</div>
						<span class="flex min-w-0 items-center gap-1 text-sm text-slate">{#if event.location}<MapPin size={12} class="shrink-0 text-dim" aria-hidden="true" /><span class="truncate">{event.location}</span>{:else}–{/if}</span>
						<div class="pointer-events-none relative z-[1] min-w-0 divide-y divide-rule [&_a]:pointer-events-auto [&_button]:pointer-events-auto [&_form]:pointer-events-auto">{@render replies(entry)}</div>
					</div>
				{/each}
			</div>
		</div>
	{:else}
		<p class="p-4 text-sm text-muted">{t('portal.no_events')}</p>
	{/each}
</div>
