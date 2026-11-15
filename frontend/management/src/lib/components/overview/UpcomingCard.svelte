<script lang="ts">
	import { CalendarDays, ChevronRight } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { eventHref, kindLabelKey, kindStyle, type EventRow } from '#lib/events/types.ts';

	/** The next events on the calendar (real data), soonest first; the full list is one click away on the calendar. */
	let { events }: { events: EventRow[] } = $props();
	const shown = $derived(events.slice(0, 6));
	const part = (iso: string, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(getLang(), opts).format(new Date(iso));
	const pill = (e: EventRow) => (e.club_wide ? t('overview.upcoming.club') : (e.teams[0]?.name ?? e.groups[0]?.name ?? ''));
</script>

<div class="card h-full">
	<div class="card-h"><CalendarDays size={17} class="shrink-0" aria-hidden="true" /><h2>{t('overview.upcoming.title')}</h2><a class="ml-auto text-sm font-semibold text-club-dark hover:underline" href="/calendar">{t('overview.upcoming.calendar')} →</a></div>
	{#if events.length}
		<ul class="divide-y divide-line">
			{#each shown as e (e.id)}
				<li>
					<a class="flex items-center gap-3 px-5 py-3 hover:bg-surface" href={eventHref(e.id)}>
						<div class="w-12 shrink-0 overflow-hidden rounded-lg border border-line bg-(--portal-panel) text-center">
							<div class="bg-club/10 font-mono text-[10px] tracking-[.08em] text-club-dark uppercase">{part(e.start, { weekday: 'short' })}</div>
							<div class="font-display text-xl leading-tight font-extrabold text-ink">{part(e.start, { day: '2-digit' })}</div>
						</div>
						<div class="min-w-0 flex-1">
							<p class="flex items-center gap-2 font-semibold text-ink"><span class="size-2 shrink-0 rounded-full {kindStyle(e.kind).dot}" title={t(kindLabelKey(e.kind))}></span><span class="truncate">{e.title}</span></p>
							<p class="truncate font-mono text-xs text-muted">{part(e.start, { hour: '2-digit', minute: '2-digit', hour12: false })}{#if e.location} · {e.location.name}{/if}{#if e.attendance.total} · {t('overview.upcoming.in', { n: e.attendance.present + e.attendance.selected, total: e.attendance.total })}{/if}</p>
						</div>
						{#if pill(e)}<span class="badge badge-sm max-w-28 shrink-0 truncate">{pill(e)}</span>{/if}
						<ChevronRight size={16} class="shrink-0 text-dim" aria-hidden="true" />
					</a>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="py-6 text-center text-sm text-muted">{t('overview.upcoming.empty')}</p>
	{/if}
</div>
