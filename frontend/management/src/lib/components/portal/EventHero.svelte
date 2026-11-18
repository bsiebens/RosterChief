<script lang="ts">
	import { Clock, MapPin } from '@lucide/svelte';
	import { formatDay, formatTime, kindLabelKey, kindStyle } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import EventBackdrop from './EventBackdrop.svelte';

	let { event, background = null, crest = null }: {
		event: { title: string; kind: string; start: string; gathering?: string | null; location: string; opponent?: string; opponent_logo?: string | null; is_home?: boolean; score_for?: number | null; score_against?: number | null };
		background?: string | null;
		crest?: string | null;
	} = $props();
</script>

<div class="portal-event-banner relative isolate flex grow min-h-40 flex-col xl:min-h-60 items-start justify-end gap-1.5 overflow-hidden px-4 py-3">
	<EventBackdrop src={background} />
	{#if crest}<img class="pointer-events-none absolute -z-10 size-44 object-contain opacity-25 xl:size-56 {event.is_home ? '-left-10' : '-right-10'}" src={crest} alt="" />{/if}
	{#if event.opponent_logo}<img class="pointer-events-none absolute -z-10 size-44 object-contain opacity-25 xl:size-56 {event.is_home ? '-right-10' : '-left-10'}" src={event.opponent_logo} alt="" />
	{:else if event.opponent}<span class="pointer-events-none absolute -z-10 opacity-25 {event.is_home ? '-right-10' : '-left-10'}" aria-hidden="true"><ClubCrest name={event.opponent} size={208} /></span>{/if}
	<span class="badge badge-sm border {kindStyle(event.kind).chip}">{t(kindLabelKey(event.kind))} · {formatDay(event.start)}</span>
	{#if event.score_for != null && event.score_against != null}<span class="absolute top-3 right-4 font-display text-5xl leading-none font-extrabold tabular-nums text-[#fff]" aria-label="{event.score_for} – {event.score_against}">{event.score_for}–{event.score_against}</span>{/if}
	<span class="font-display text-2xl leading-none font-extrabold wrap-break-word uppercase sm:text-3xl">{event.title}</span>
	<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
	<span class="inline-flex items-center gap-1.5"><Clock size={14} aria-hidden="true" />{#if event.gathering}{t('portal.meet_time', { time: formatTime(event.gathering) })} · {t('portal.start_time', { time: formatTime(event.start) })}{:else}{formatTime(event.start)}{/if}</span>
	{#if event.location}<span class="inline-flex items-center gap-1.5"><MapPin size={14} class="shrink-0" aria-hidden="true" />{event.location}</span>{/if}
	</div>
</div>
