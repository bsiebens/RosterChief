<script lang="ts">
	import { MapPin, Pencil, Repeat } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { eventEditHref, eventHref, formatTime, kindLabelKey, kindStyle, type AttendanceCounts, type EventRow } from '#lib/events/types.ts';

	/**
	 * One event in the calendar. `chip`: one line for a month cell; `card`: the full card for the week and agenda views.
	 * The whole card links to the event page (stretched link); the edit pencil sits above it and only shows with `can_edit`.
	 */
	let { event, variant = 'card' }: { event: EventRow; variant?: 'chip' | 'card' } = $props();

	const style = $derived(kindStyle(event.kind));
	const time = $derived(event.end ? `${formatTime(event.start)} – ${formatTime(event.end)}` : formatTime(event.start));
	const audience = $derived(event.club_wide ? t('events.whole_club') : [...event.teams, ...event.groups].map((x) => x.name).join(', '));

	function summary(c: AttendanceCounts): string {
		if (!c.total) return '';
		const parts = [t('events.att.sum_in', { n: c.present + c.selected }), t('events.att.sum_out', { n: c.absent + c.excused + c.not_selected })];
		if (c.maybe) parts.push(t('events.att.sum_maybe', { n: c.maybe }));
		parts.push(t('events.att.sum_none', { n: c.no_response }));
		return parts.join(' · ');
	}
	const att = $derived(summary(event.attendance));
	const label = $derived([time, event.title, t(kindLabelKey(event.kind)), audience, event.location?.name ?? '', event.cancelled ? t('events.cancelled') : ''].filter(Boolean).join(', '));
</script>

{#if variant === 'chip'}
	<div class="group relative flex min-w-0 items-center gap-1.5 rounded px-1.5 py-0.5 text-xs hover:bg-paper focus-within:bg-paper {event.cancelled ? 'opacity-60' : ''}">
		<span class="h-2 w-2 shrink-0 rounded-full {style.dot}" aria-hidden="true"></span>
		<span class="shrink-0 font-mono text-[11px] text-muted">{formatTime(event.start)}</span>
		<a href={eventHref(event.id)} class="min-w-0 flex-1 truncate font-semibold text-ink after:absolute after:inset-0 focus:outline-none {event.cancelled ? 'line-through' : ''}" aria-label={label} title={label}>{event.title}</a>
		{#if event.series_id}<Repeat size={11} class="shrink-0 text-dim" aria-hidden="true" />{/if}
	</div>
{:else}
	<article class="relative rounded-lg border border-l-4 border-line bg-white p-3 transition-shadow hover:shadow-md focus-within:shadow-md {style.bar} {event.cancelled ? 'bg-subhead' : ''}">
		<div class="flex items-start justify-between gap-2">
			<div class="min-w-0">
				<p class="font-mono text-xs text-muted">{time}</p>
				<h3 class="mt-0.5 font-semibold text-ink">
					<a href={eventHref(event.id)} class="after:absolute after:inset-0 after:rounded-lg focus:outline-none {event.cancelled ? 'line-through decoration-2' : ''}" aria-label={label}>{event.title}</a>
				</h3>
			</div>
			<div class="relative z-[1] flex shrink-0 items-center gap-1">
				{#if event.series_id}<span class="text-dim" title={t('events.series')}><Repeat size={14} aria-label={t('events.series')} /></span>{/if}
				{#if event.can_edit}<a class="btn btn-ghost btn-xs btn-square" href={eventEditHref(event.id)} aria-label={t('events.edit_named', { name: event.title })} title={t('common.edit')}><Pencil size={13} /></a>{/if}
			</div>
		</div>
		<div class="mt-1.5 flex flex-wrap items-center gap-1.5">
			<span class="badge badge-xs border {style.chip}">{t(kindLabelKey(event.kind))}</span>
			{#if event.cancelled}<span class="badge badge-xs badge-error">{t('events.cancelled')}</span>{/if}
			{#if audience}<span class="truncate text-xs text-slate">{audience}</span>{/if}
		</div>
		{#if event.location || event.opponent}
			<p class="mt-1 flex items-center gap-1 truncate text-xs text-muted">
				{#if event.opponent}<span class="font-semibold">{t('events.vs', { name: event.opponent.name })}</span>{/if}
				{#if event.location}<MapPin size={12} class="shrink-0" aria-hidden="true" /><span class="truncate">{event.location.name}{event.location.city ? `, ${event.location.city}` : ''}</span>{/if}
			</p>
		{/if}
		{#if att}<p class="mt-1 font-mono text-[11px] text-muted">{att}</p>{/if}
	</article>
{/if}
