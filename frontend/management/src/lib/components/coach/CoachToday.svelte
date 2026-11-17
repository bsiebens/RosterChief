<script lang="ts">
	import { ChevronRight, ClipboardList, ClipboardCheck, Users } from '@lucide/svelte';
	import { formatDateTime, formatDay, formatTime, kindLabelKey, kindStyle } from '#lib/events/types.ts';
	import { attention, isUpcoming, lineupChip, remind } from '#lib/coach.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { CoachEvent } from '#lib/portal.ts';
	import PortalEventDate from '#lib/components/PortalEventDate.svelte';
	import EventBackdrop from '#lib/components/portal/EventBackdrop.svelte';
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

	const PILL = 'inline-flex h-6 shrink-0 items-center rounded-full px-2.5 text-[13px] font-bold whitespace-nowrap';
	const chipPill = (cls: string) => (cls === 'badge-warning' ? 'bg-warn-bg text-warn-text' : cls === 'badge-info' ? 'bg-info-bg text-info-text' : 'pill-muted');
	const weekday = (iso: string) => new Date(iso).toLocaleDateString(getLang(), { weekday: 'short' });
	const needTitle = (item: (typeof needs.items)[number]) => (item.type === 'replies' ? t(item.event.unanswered === 1 ? 'coach.replies_one' : 'coach.replies_many', { n: item.event.unanswered }) : t(item.type === 'sheet' ? 'coach.game_sheet_missing' : 'coach.lineup_unpublished'));
	const needHref = (item: (typeof needs.items)[number]) => (item.type === 'lineup' ? `/calendar/events/${item.event.id}?tab=lineup&view=coach` : item.type === 'sheet' ? `/calendar/events/${item.event.id}?view=coach` : href(item.event));

	async function nudge(event: CoachEvent) {
		busy = event.id;
		const result = await remind(event.id);
		notice = result.ok ? t(result.count ? 'coach.reminded' : 'coach.remind_none', { n: result.count }) : result.message || t('common.error.generic');
		busy = '';
	}
</script>

{#snippet heading(label: string, link?: { href: string; text: string }, count?: number)}
	<h2 class="flex items-center gap-2 px-0.5 font-display text-xl font-extrabold tracking-[.02em] text-ink uppercase">
		<span class="flex-1">{label}</span>
		{#if count}<span class="grid min-w-5.5 place-items-center rounded-full bg-club px-1.5 font-sans text-[13px] leading-[1.4rem] font-bold tracking-normal text-club-content normal-case">{count}</span>{/if}
		{#if link}<a class="font-sans text-sm font-semibold tracking-normal text-club-dark normal-case" href={link.href}>{link.text}</a>{/if}
	</h2>
{/snippet}

<!-- Phone: the board's Today: the next session as a photo card with the three counts and the two actions, what needs you, this week. -->
<div class="grid min-w-0 gap-5 md:hidden">
	{#if session}
		<section class="portal-event-banner relative isolate flex min-h-64 flex-col justify-end gap-3 overflow-hidden rounded-box p-[18px] text-[#fff]" data-kind={session.kind} aria-label={t(isToday ? 'portal.today_session' : 'portal.next_session')}>
			<EventBackdrop src={background} />
			<a class="absolute inset-0 z-0" href={href(session)} aria-label={session.title}></a>
			<span class="pointer-events-none relative z-10 flex items-center gap-2"><span class="kind-pill inline-flex h-[26px] items-center rounded-full px-2.5 text-[13px] font-bold">{t(kindLabelKey(session.kind))} · {isToday ? t('portal.today') : formatDay(session.start)}</span></span>
			<span class="pointer-events-none relative z-10 font-display text-4xl leading-[.98] font-extrabold uppercase">{formatTime(session.start)}{place(session)}</span>
			<span class="pointer-events-none relative z-10 text-[15px]">{session.title}</span>
			<div class="pointer-events-none relative z-10 grid grid-cols-3 gap-2">
				{#each [[session.going, 'portal.going'], [session.out, 'portal.out'], [session.unanswered, 'coach.waiting']] as [count, label] (label)}
					<div class="rounded-xl border border-[#fff]/30 bg-[#fff]/15 px-2 py-2.5 text-center"><b class="block font-display text-2xl leading-none font-extrabold">{count ?? 0}</b><span class="mt-1 block text-xs">{t(String(label))}</span></div>
				{/each}
			</div>
			{#if session.can_manage_attendance}
				<div class="relative z-10 flex gap-2">
					<a class="btn min-h-12 flex-[2] gap-2 border-0 bg-[#fff] text-[#0b0d10]" href="/calendar/events/{session.id}?tab=attendance&view=coach"><ClipboardCheck size={16} aria-hidden="true" />{t('portal.take_attendance')}</a>
					{#if isUpcoming(session) && session.unanswered > 0}<button type="button" class="btn min-h-12 flex-1 border-[#fff]/40 !bg-[#fff]/15 !text-[#fff]" disabled={busy === session.id} onclick={() => nudge(session)}>{t('coach.remind')}</button>{/if}
				</div>
			{/if}
		</section>
		{#if notice}<p class="-mt-2 px-1 text-sm text-muted" role="status">{notice}</p>{/if}
	{:else}
		<p class="card card-body text-sm text-muted">{t('portal.no_events')}</p>
	{/if}

	{#if needs.items.length}
		<section class="grid gap-2">
			{@render heading(t('coach.needs_attention'), undefined, needs.items.length)}
			<div class="card divide-y divide-rule overflow-hidden">
				{#each needs.items as item (item.type + item.event.id)}
					<a class="flex min-h-16 items-center gap-3 px-3.5 py-2.5" href={needHref(item)}>
						<span class="grid size-10 shrink-0 place-items-center rounded-box {item.type === 'replies' ? 'bg-danger-bg text-danger' : 'bg-warn-bg text-warn-text'}">{#if item.type === 'replies'}<Users size={18} aria-hidden="true" />{:else}<ClipboardList size={18} aria-hidden="true" />{/if}</span>
						<span class="min-w-0 flex-1"><b class="block text-base leading-snug text-ink">{needTitle(item)}</b><span class="block text-sm text-muted">{item.event.title} · {formatDay(item.event.start)}</span></span>
						<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
					</a>
				{/each}
			</div>
		</section>
	{/if}

	<section class="grid gap-2">
		{@render heading(t('coach.this_week'), { href: '/coach?tab=calendar', text: t('coach.all_events') })}
		<div class="card divide-y divide-rule overflow-hidden">
			{#each week as event (event.id)}
				{@const chip = lineupChip(event, formatDateTime)}
				<a class="flex min-h-15 items-center gap-3 border-l-[3px] px-3.5 py-2.5 {kindStyle(event.kind).bar}" href={href(event)}>
					<span class="w-12 shrink-0 text-center leading-none"><b class="block font-display text-[1.625rem] font-extrabold text-ink">{new Date(event.start).getDate()}</b><span class="font-mono text-[11px] tracking-[.06em] text-muted uppercase">{weekday(event.start)}</span></span>
					<span class="min-w-0 flex-1"><b class="block text-base leading-snug font-semibold text-ink">{event.title}</b><span class="block text-sm text-muted">{formatTime(event.start)}{event.can_manage_attendance ? ' · ' + t('coach.n_going', { n: event.going }) : ''}</span></span>
					{#if chip}<span class="{PILL} {chipPill(chip.cls)}">{t(chip.key, { when: chip.text ?? '' })}</span>{:else}<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />{/if}
				</a>
			{:else}
				<p class="px-4 py-3 text-sm text-muted">{t('portal.no_events')}</p>
			{/each}
		</div>
	</section>
</div>

<div class="hidden min-w-0 items-start gap-4 md:grid md:grid-cols-[minmax(0,5fr)_minmax(0,4fr)]">
	{#if session}
		<article class="card overflow-hidden">
			<h2 class="card-title px-4 pt-3 pb-1 md:pb-3">{t(isToday ? 'portal.today_session' : 'portal.next_session')}</h2>
			<a class="block" href={href(session)}><EventHero event={{ ...session, is_home: session.is_home }} {background} {crest} /></a>
			{#if session.notes}<p class="px-4 pt-3 text-sm whitespace-pre-line text-slate">{session.notes}</p>{/if}
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
						{#if lineupChip(event, formatDateTime)}{@const chip = lineupChip(event, formatDateTime)!}<span class="badge badge-sm {chip.cls}">{t(chip.key, { when: chip.text ?? '' })}</span>{:else}<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />{/if}
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
