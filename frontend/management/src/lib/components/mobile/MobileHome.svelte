<script lang="ts">
	import type { Snippet } from 'svelte';
	import { CalendarDays, Check, ChevronRight, Ellipsis, FileText, Wallet } from '@lucide/svelte';
	import EventBackdrop from '#lib/components/portal/EventBackdrop.svelte';
		import { formatDay, formatTime, kindLabelKey } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { PortalArticle, PortalEvent, PortalEventDisplay } from '#lib/portal.ts';
	import { statusPill } from './pills.ts';

	/**
	 * Home on a phone, as on the design boards. The header (greeting, person chip, bell) is the shell's. Here: the soonest event as a large photo
	 * card with the family's answers under it (other children's events as slim photo cards), "Needs you" as four shortcuts with counts, this week
	 * as dated rows with a status pill, the games being followed and the latest news as one card.
	 */
	let {
		people,
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
		nextEvents: PortalEvent[][];
		background: string | null;
		crest: string | null | undefined;
		counts: { replies: number; documents: number; payments: number; forms: number };
		weekItems: { key: string; event: PortalEventDisplay; badges: { text: string; cls: string; status: string; who: string }[] }[];
		news: PortalArticle[];
		eventLink: (id: string) => string;
		firstName: (id: string) => string;
		rsvp: Snippet<[PortalEvent, boolean, boolean]>;
		following: Snippet;
	} = $props();

	const open = $derived(counts.replies + counts.documents + counts.payments + counts.forms);
	const tiles = $derived([
		{ key: 'replies', href: '/parent?tab=calendar', label: t('portal.tile_replies'), n: counts.replies, icon: CalendarDays, tone: 'bg-danger-bg text-danger' },
		{ key: 'documents', href: '/parent?tab=documents', label: t('portal.tile_documents'), n: counts.documents, icon: FileText, tone: 'bg-warn-bg text-warn-text' },
		{ key: 'payments', href: '/parent?tab=payments', label: t('portal.tile_payments'), n: counts.payments, icon: Wallet, tone: 'bg-info-bg text-info-text' },
		{ key: 'more', href: '/parent?tab=me', label: t('portal.tile_more'), n: counts.forms, icon: Ellipsis, tone: 'bg-subhead text-ink' }
	]);
	const timeLine = (event: PortalEventDisplay) => (event.gathering ? `${t('portal.meet_time', { time: formatTime(event.gathering) })} · ${t('portal.start_time', { time: formatTime(event.start) })}` : formatTime(event.start));
	const when = (event: PortalEventDisplay) => `${formatDay(event.start)} · ${formatTime(event.start)}`;
	const weekday = (iso: string) => new Date(iso).toLocaleDateString(getLang(), { weekday: 'short' });
	// One row per event; the family's children answer on it together, each with their first name in full and status.
	const weekRows = $derived(weekItems.slice(0, 5).map((item) => ({ key: item.key, event: item.event, who: item.badges.map((b) => ({ name: b.who, pill: statusPill(b.status) })) })));
	const translation = (article: PortalArticle) => article.translations[getLang() === 'nl' ? 'nl' : 'en'];
</script>

{#snippet heading(label: string, link?: { href: string; text: string }, count?: number)}
	<h2 class="flex items-center gap-2 px-0.5 font-display text-xl font-extrabold tracking-[.02em] text-ink uppercase">
		<span class="flex-1">{label}</span>
		{#if count}<span class="grid min-w-5.5 place-items-center rounded-full bg-club px-1.5 font-sans text-[13px] leading-[1.4rem] font-bold tracking-normal text-club-content normal-case">{count}</span>{/if}
		{#if link}<a class="font-sans text-sm font-semibold tracking-normal text-club-dark normal-case" href={link.href}>{link.text}</a>{/if}
	</h2>
{/snippet}

<div class="grid min-w-0 gap-5">
	<section class="grid gap-2" aria-label={t('portal.next_up')}>
		{#each nextEvents as rows, i (rows[0].event_id)}
			{@const event = rows[0]}
			{#if i === 0}
				<a class="portal-event-banner relative isolate flex min-h-60 flex-col justify-end gap-3 overflow-hidden rounded-[18px] border-s-0! p-[18px] text-[#fff]" href={eventLink(event.event_id)} data-sveltekit-reset="false" aria-label={event.title}>
					<EventBackdrop src={background} />
					<span class="flex items-center gap-2"><span class="inline-flex h-[26px] items-center rounded-full border border-[#fff]/40 bg-[#fff]/20 px-2.5 text-[13px] font-bold">{t(kindLabelKey(event.kind))} · {when(event)}</span></span>
					<span class="font-display text-4xl leading-[.98] font-extrabold uppercase">{event.title}</span>
					<span class="text-[15px]">{timeLine(event)}{event.location ? ' · ' + event.location : ''}</span>
					<span class="flex flex-wrap items-center gap-2">
						{#each rows as row (row.id)}
							{@const answered = row.status === 'present' || row.status === 'selected'}
							<span class="inline-flex h-[30px] items-center gap-1.5 rounded-full px-3 text-sm font-bold {answered ? 'bg-[#0a6b42] text-[#fff]' : 'bg-club text-club-content'}">
								{#if answered}<Check size={15} aria-hidden="true" />{/if}{people.length > 1 ? `${firstName(row.member_id)} · ` : ''}{statusPill(row.status).text}
							</span>
						{/each}
						<span class="ml-auto font-bold underline">{t('portal.event_details')}</span>
					</span>
				</a>
			{:else}
				<a class="portal-event-banner relative isolate flex min-h-24 flex-col justify-end gap-1.5 overflow-hidden rounded-box px-4 py-3 text-[#fff]" href={eventLink(event.event_id)} data-sveltekit-reset="false">
					<EventBackdrop src={background} />
					<span class="truncate font-mono text-xs tracking-[.04em] text-[#fff]/90 uppercase">{when(event)}</span>
					<span class="font-display text-2xl leading-none font-extrabold uppercase">{event.title}</span>
					<span class="flex flex-wrap items-center gap-1.5">
						{#each rows as row (row.id)}
							{@const p = statusPill(row.status)}
							<span class="inline-flex h-7 items-center gap-1.5 rounded-full bg-[#fff]/15 pr-1 pl-3 text-[13px] font-semibold text-[#fff]">{people.length > 1 ? firstName(row.member_id) : ''}<span class="{p.cls} ml-1">{p.text}</span></span>
						{/each}
					</span>
				</a>
			{/if}
		{:else}
			<div class="card card-body flex-row items-center gap-3"><Check size={20} class="text-ok" aria-hidden="true" /><p class="text-sm text-muted">{t('portal.no_events')}</p></div>
		{/each}
	</section>

	<section class="grid gap-2" aria-label={t('portal.needs_you')}>
		{@render heading(t('portal.needs_you'), undefined, open)}
		<div class="grid grid-cols-4 gap-2.5">
			{#each tiles as tile (tile.key)}
				<a class="card relative grid min-h-21 place-items-center content-center gap-1.5 px-1 py-2 text-center text-[13px] font-semibold text-ink" href={tile.href}>
					{#if tile.n > 0}<span class="absolute top-1.5 right-2 grid min-w-5 place-items-center rounded-full bg-club px-1 text-xs leading-5 font-bold text-club-content" aria-label={t('portal.todo_count', { n: tile.n })}>{tile.n}</span>{/if}
					<span class="grid size-9 place-items-center rounded-box {tile.tone}"><tile.icon size={18} aria-hidden="true" /></span>
					{tile.label}
				</a>
			{/each}
		</div>
	</section>

	{#if weekRows.length}
		<section class="grid gap-2" aria-label={t('portal.next_fourteen_days')}>
			{@render heading(t('portal.this_week'), { href: '/parent?tab=calendar', text: t('portal.agenda') })}
			<div class="card divide-y divide-rule overflow-hidden">
				{#each weekRows as row (row.key)}
					<a class="flex min-h-15 items-center gap-3 px-3.5 py-2.5" href={eventLink(row.event.event_id)} data-sveltekit-reset="false">
						<span class="w-12 shrink-0 text-center leading-none"><b class="block font-display text-[1.625rem] font-extrabold text-ink">{new Date(row.event.start).getDate()}</b><span class="font-mono text-[11px] tracking-[.06em] text-muted uppercase">{weekday(row.event.start)}</span></span>
						<span class="min-w-0 flex-1">
							<b class="block text-base leading-snug font-semibold text-ink">{row.event.title}</b>
							<span class="block text-sm text-muted">{timeLine(row.event)}</span>
							{#if people.length > 1 && row.who.length}
								<span class="mt-1.5 flex flex-wrap gap-x-3 gap-y-1.5">{#each row.who as w (w.name)}<span class="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">{w.name}<span class={w.pill.cls}>{w.pill.text}</span></span>{/each}</span>
							{/if}
						</span>
						{#if people.length <= 1 && row.who.length}<span class={row.who[0].pill.cls}>{row.who[0].pill.text}</span>{/if}
					</a>
				{/each}
			</div>
		</section>
	{/if}

	{@render following()}

	{#if news.length}
		{@const article = news[0]}
		<section class="grid gap-2" aria-label={t('portal.news')}>
			{@render heading(t('portal.latest_news'), { href: '/parent?tab=news', text: t('portal.all_news') })}
			<a class="card flex items-start gap-3 p-3.5" href={`/parent?tab=news&article=${article.id}`} data-sveltekit-reset="false">
				<span class="flex min-w-0 flex-1 flex-col gap-0.5">
					<span class="font-mono text-[11px] tracking-[.04em] text-muted uppercase">{(article.teams ?? []).join(', ') || t('portal.news')}{article.published_at ? ` · ${formatDay(article.published_at)}` : ''}</span>
					<b class="mt-0.5 text-lg leading-tight font-semibold text-ink">{translation(article).title}</b>
					{#if translation(article).excerpt}<span class="line-clamp-2 text-sm text-muted">{translation(article).excerpt}</span>{/if}
				</span>
				<ChevronRight size={18} class="mt-1 shrink-0 text-muted" aria-hidden="true" />
			</a>
		</section>
	{/if}
</div>
