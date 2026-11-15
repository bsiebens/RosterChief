<script lang="ts">
	import { ChevronRight, ClipboardList, ClipboardCheck, Users } from '@lucide/svelte';
	import { formatDay, formatTime } from '#lib/events/types.ts';
	import { attention, isUpcoming, remind } from '#lib/coach.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { CoachEvent } from '#lib/portal.ts';
	import PortalEventDate from '#lib/components/PortalEventDate.svelte';
	import EventHero from '#lib/components/portal/EventHero.svelte';

	let { events, background = null, crest = null }: { events: CoachEvent[]; background?: string | null; crest?: string | null } = $props();
	let busy = $state('');
	let notice = $state('');
	const session = $derived(events.find((event) => !event.cancelled && Date.parse(event.start) >= Date.now()) ?? events.findLast((event) => !event.cancelled));
	const isToday = $derived(!!session && new Date(session.start).toDateString() === new Date().toDateString());
	const week = $derived(events.filter((event) => isUpcoming(event) && event.id !== session?.id && Date.parse(event.start) < Date.now() + 7 * 864e5).slice(0, 5));
	const needs = $derived(attention(events));
	const href = (event: CoachEvent) => (event.can_manage_attendance ? `/calendar/events/${event.id}?tab=attendance&view=coach` : `/calendar/events/${event.id}?view=coach`);
	const place = (event: CoachEvent) => (event.location ? ' · ' + event.location : '');

	async function nudge(event: CoachEvent) {
		busy = event.id;
		const result = await remind(event.id);
		notice = result.ok ? t(result.count ? 'coach.reminded' : 'coach.remind_none', { n: result.count }) : result.message || t('common.error.generic');
		busy = '';
	}
</script>

<div class="grid min-w-0 items-start gap-4 md:grid-cols-[minmax(0,5fr)_minmax(0,4fr)]">
	{#if session}
		<article class="card overflow-hidden">
			<h2 class="card-title px-4 pt-3 pb-1 md:pb-3">{t(isToday ? 'portal.today_session' : 'portal.next_session')}</h2>
			<a class="block" href={href(session)}><EventHero event={{ ...session, is_home: session.is_home }} {background} {crest} /></a>
			<div class="flex flex-wrap items-center gap-x-5 gap-y-3 bg-subhead px-4 py-3">
				{#each [[session.going, 'portal.going'], [session.out, 'portal.out'], [session.unanswered, 'coach.waiting']] as [count, label] (label)}
					<div class="min-w-14">
						<div class="font-display text-2xl leading-none font-extrabold text-ink">{count ?? 0}</div>
						<div class="mt-1 font-mono text-[11px] tracking-[.08em] text-muted uppercase">{t(String(label))}</div>
					</div>
				{/each}
				{#if session.can_manage_attendance}
					<a class="btn btn-primary ml-auto min-h-11 gap-2" href="/calendar/events/{session.id}?tab=attendance&view=coach"><ClipboardCheck size={16} aria-hidden="true" />{t('portal.take_attendance')}</a>
				{/if}
			</div>
		</article>
	{:else}
		<p class="card card-body text-sm text-muted">{t('portal.no_events')}</p>
	{/if}

	<div class="grid min-w-0 gap-4">
		<section class="card">
			<div class="card-h"><h2>{t('coach.this_week')}</h2><a class="link-more ml-auto" href="/coach?tab=calendar">{t('shell.nav.events')} →</a></div>
			<div class="divide-y divide-rule">
				{#each week as event (event.id)}
					<a class="flex min-h-14 items-center gap-3 px-4 py-2 hover:bg-subhead" href={href(event)}>
						<PortalEventDate start={event.start} compact />
						<span class="min-w-0 flex-1">
							<b class="block truncate text-sm text-ink">{event.title}</b>
							<span class="block text-xs text-muted">{event.lineup && !event.lineup_published ? t('coach.time_lineup', { time: formatTime(event.start) }) : t('coach.time_going', { time: formatTime(event.start), n: event.going })}</span>
						</span>
						{#if event.lineup && !event.lineup_published}<span class="badge badge-sm badge-warning">{t('portal.lineup')}</span>{:else}<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />{/if}
					</a>
				{:else}
					<p class="px-4 py-3 text-sm text-muted">{t('portal.no_events')}</p>
				{/each}
			</div>
		</section>

		{#if needs.items.length}
			<section class="card">
				<div class="card-h"><h2>{t('coach.needs_attention')}</h2></div>
				<div class="divide-y divide-rule">
					{#each needs.items as item (item.type + item.event.id)}
						{@const event = item.event}
						<div class="flex min-h-13 items-center gap-3 px-4 py-2">
							{#if item.type === 'replies'}<Users size={18} class="shrink-0 text-muted" aria-hidden="true" />{:else}<ClipboardList size={18} class="shrink-0 text-muted" aria-hidden="true" />{/if}
							<a class="min-w-0 flex-1 hover:text-brand" href={item.type === 'lineup' ? `/calendar/events/${event.id}?tab=lineup&view=coach` : item.type === 'sheet' ? `/calendar/events/${event.id}?view=coach` : href(event)}>
								<b class="block text-sm text-ink">{item.type === 'replies' ? t(event.unanswered === 1 ? 'coach.replies_one' : 'coach.replies_many', { n: event.unanswered }) : t(item.type === 'sheet' ? 'coach.game_sheet_missing' : 'coach.lineup_unpublished')}</b>
								<span class="block truncate text-xs text-muted">{event.title} · {formatDay(event.start)}</span>
							</a>
							{#if item.type === 'replies'}
								<button type="button" class="btn btn-outline btn-sm min-h-9 shrink-0" disabled={busy === event.id} onclick={() => nudge(event)}>{t('coach.remind')}</button>
							{:else}
								<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
							{/if}
						</div>
					{/each}
				</div>
				{#if notice}<p class="border-t border-rule px-4 py-2 text-sm text-muted" role="status">{notice}</p>{/if}
				{#if needs.more}<div class="flex justify-end border-t border-rule px-4"><a class="link-more" href="/coach?tab=calendar">{t('coach.see_all')} →</a></div>{/if}
			</section>
		{/if}
	</div>
</div>
