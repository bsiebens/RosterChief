<script lang="ts">
	import type { Snippet } from 'svelte';
	import { CalendarDays, Check, ChevronRight, Ellipsis, FileText, Wallet } from '@lucide/svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import PersonFilter from '#lib/components/PersonFilter.svelte';
	import EventBackdrop from '#lib/components/portal/EventBackdrop.svelte';
	import EventHero from '#lib/components/portal/EventHero.svelte';
	import News from '#lib/components/portal/News.svelte';
	import { formatDay, formatTime, statusLabelKey } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { PortalArticle, PortalEvent, PortalEventDisplay } from '#lib/portal.ts';

	/**
	 * Home on a phone. One job: what is next, and what needs me. The soonest event gets the large photo card (with the family's answers under
	 * it); another child's next event is a slim photo card. "Needs you" is a menu of four shortcuts with counts instead of a list, then the
	 * coming days with meet-up times, the games being followed and the latest news.
	 */
	let {
		people,
		selected = $bindable(''),
		viewer,
		greeting,
		dateLabel,
		nextEvents,
		background,
		crest,
		counts,
		weekItems,
		news,
		eventLink,
		firstName,
		rsvp,
		following
	}: {
		people: { id: string; name: string; first_name: string; photo_url: string | null }[];
		selected: string;
		viewer: string;
		greeting: string;
		dateLabel: string;
		nextEvents: PortalEvent[][];
		background: string | null;
		crest: string | null | undefined;
		counts: { replies: number; documents: number; payments: number; forms: number };
		weekItems: { key: string; event: PortalEventDisplay; badges: { text: string; cls: string }[] }[];
		news: PortalArticle[];
		eventLink: (id: string) => string;
		firstName: (id: string) => string;
		rsvp: Snippet<[PortalEvent, boolean, boolean]>;
		following: Snippet;
	} = $props();

	const going = (row: PortalEvent) => row.status === 'present' || row.status === 'selected';
	const open = $derived(counts.replies + counts.documents + counts.payments + counts.forms);
	const tiles = $derived([
		{ key: 'replies', href: '/parent?tab=calendar', label: t('portal.tile_replies'), n: counts.replies, icon: CalendarDays, tone: 'bg-danger-bg text-danger' },
		{ key: 'documents', href: '/parent?tab=documents', label: t('portal.tile_documents'), n: counts.documents, icon: FileText, tone: 'bg-warn-bg text-warn-text' },
		{ key: 'payments', href: '/parent?tab=payments', label: t('portal.tile_payments'), n: counts.payments, icon: Wallet, tone: 'bg-info-bg text-info-text' },
		{ key: 'more', href: '/parent?tab=me', label: t('portal.tile_more'), n: counts.forms, icon: Ellipsis, tone: 'bg-subhead text-ink' }
	]);
	const timeLine = (event: PortalEventDisplay) => (event.gathering ? `${t('portal.meet_time', { time: formatTime(event.gathering) })} · ${t('portal.start_time', { time: formatTime(event.start) })}` : formatTime(event.start));
	const when = (event: PortalEventDisplay) => `${formatDay(event.start)} · ${formatTime(event.start)}`;
	const answer = (row: PortalEvent) => (going(row) ? { cls: 'bg-ok text-white', text: t(statusLabelKey('present')) } : row.status === 'maybe' ? { cls: 'bg-warn-bg text-warn-text', text: t(statusLabelKey(row.status)) } : { cls: 'bg-club text-club-content', text: t('portal.reply') });
</script>

<div class="grid min-w-0 gap-5">
	<div class="flex items-baseline justify-between gap-2">
		<h1 class="font-display text-3xl leading-none font-extrabold text-ink uppercase">{greeting}</h1>
		<p class="font-mono text-[11px] tracking-[.12em] text-muted uppercase">{dateLabel}</p>
	</div>
	{#if people.length > 1}<PersonFilter {people} bind:selected {viewer} />{/if}

	<section class="grid gap-2" aria-label={t('portal.next_up')}>
		{#each nextEvents as rows, i (rows[0].event_id)}
			{@const event = rows[0]}
			{#if i === 0}
				<article class="card overflow-hidden">
					<a class="flex" href={eventLink(event.event_id)} data-sveltekit-reset="false" aria-label={event.title}><EventHero {event} {background} {crest} /></a>
					<div class="grid gap-1 border-t border-sidebar-hairline bg-sidebar-hairline px-4 py-3 text-sidebar-fg">
						{#each rows as row (row.id)}{@render rsvp(row, true, true)}{/each}
						<a class="inline-flex min-h-11 items-center text-sm font-semibold text-sidebar-fg hover:underline" href={eventLink(event.event_id)} data-sveltekit-reset="false">{t('portal.event_details')} →</a>
					</div>
				</article>
			{:else}
				<a class="portal-event-banner relative isolate flex min-h-24 flex-col justify-end gap-1 overflow-hidden rounded-box px-4 py-3 text-white" href={eventLink(event.event_id)} data-sveltekit-reset="false">
					<EventBackdrop src={background} />
					<span class="flex items-center gap-2 text-xs font-semibold">
						<ClubCrest name={rows[0].member_id ? firstName(rows[0].member_id) : ''} logoUrl={people.find((p) => p.id === rows[0].member_id)?.photo_url} size={24} />
						<span class="min-w-0 flex-1 truncate">{rows.map((row) => firstName(row.member_id)).join(', ')} · {when(event)}</span>
						{#each rows as row (row.id)}{@const a = answer(row)}<span class="rounded-full px-2.5 py-1 text-[11px] font-bold {a.cls}">{a.text}</span>{/each}
					</span>
					<span class="font-display text-xl leading-none font-extrabold uppercase">{event.title}</span>
					<span class="text-xs text-white/85">{timeLine(event)}{event.location ? ' · ' + event.location : ''}</span>
				</a>
			{/if}
		{:else}
			<div class="card card-body flex-row items-center gap-3"><Check size={20} class="text-ok" aria-hidden="true" /><p class="text-sm text-muted">{t('portal.no_events')}</p></div>
		{/each}
	</section>

	<section class="grid gap-2" aria-label={t('portal.attention')}>
		<h2 class="flex items-center gap-2 font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.attention')}{#if open > 0}<span class="badge badge-sm badge-error">{open}</span>{/if}</h2>
		<div class="grid grid-cols-4 gap-2">
			{#each tiles as tile (tile.key)}
				<a class="card relative grid min-h-22 place-items-center content-center gap-1.5 px-1 py-2 text-center text-xs font-semibold text-ink" href={tile.href}>
					{#if tile.n > 0}<span class="absolute top-1.5 right-2 grid min-w-5 place-items-center rounded-full bg-club px-1 text-[11px] leading-5 font-bold text-club-content" aria-label={t('portal.todo_count', { n: tile.n })}>{tile.n}</span>{/if}
					<span class="grid size-9 place-items-center rounded-box {tile.tone}"><tile.icon size={18} aria-hidden="true" /></span>
					{tile.label}
				</a>
			{/each}
		</div>
	</section>

	{#if weekItems.length}
		<section class="grid gap-2" aria-label={t('portal.next_fourteen_days')}>
			<h2 class="flex items-baseline font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.this_week')}<a class="link-more ml-auto" href="/parent?tab=calendar">{t('portal.agenda')} →</a></h2>
			<div class="card divide-y divide-rule overflow-hidden">
				{#each weekItems.slice(0, 4) as { key, event, badges } (key)}
					<a class="flex min-h-15 items-center gap-3 px-3 py-2" href={eventLink(event.event_id)} data-sveltekit-reset="false">
						<span class="w-12 shrink-0 text-center leading-none"><b class="block font-display text-2xl font-extrabold text-ink">{new Date(event.start).getDate()}</b><span class="font-mono text-[11px] tracking-[.06em] text-muted uppercase">{new Date(event.start).toLocaleDateString(undefined, { weekday: 'short' })}</span></span>
						<span class="min-w-0 flex-1"><span class="block truncate font-semibold text-ink">{event.title}</span><span class="block truncate text-sm text-muted">{timeLine(event)}</span></span>
						<span class="flex shrink-0 flex-wrap justify-end gap-1">{#each badges.slice(0, 2) as badge, j (j)}<span class="badge badge-sm border {badge.cls}">{badge.text}</span>{/each}</span>
						<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
					</a>
				{/each}
			</div>
		</section>
	{/if}

	{@render following()}

	{#if news.length}
		<section class="grid gap-2" aria-label={t('portal.news')}>
			<h2 class="flex items-baseline font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.latest_news')}<a class="link-more ml-auto" href="/parent?tab=news">{t('portal.all_news')} →</a></h2>
			<News articles={news.slice(0, 1)} home />
		</section>
	{/if}
</div>
