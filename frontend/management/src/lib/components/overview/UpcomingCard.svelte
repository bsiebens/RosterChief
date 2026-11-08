<script lang="ts">
	import { ArrowRight, ChevronRight } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { eventHref, kindLabelKey, kindStyle, type EventRow } from '#lib/events/types.ts';

	/**
	 * The next events on the calendar (real data), soonest first. As many rows are shown as fit the card (its height comes from
	 * the neighbouring cards), so the list never needs to scroll; the full list is one click away on the calendar.
	 */
	let { events }: { events: EventRow[] } = $props();
	const ROW_PX = 62;
	let boxHeight = $state(0);
	const fit = $derived(boxHeight ? Math.max(3, Math.floor(boxHeight / ROW_PX)) : 5);
	const shown = $derived(events.slice(0, fit));
	const part = (iso: string, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(getLang(), opts).format(new Date(iso));
	const pill = (e: EventRow) => (e.club_wide ? t('overview.upcoming.club') : (e.teams[0]?.name ?? e.groups[0]?.name ?? ''));
</script>

<div class="card card-body h-full gap-1 border-ink bg-ink text-white">
	<h2 class="mb-2 font-display text-lg font-extrabold tracking-[.04em] uppercase">{t('overview.upcoming.title')}</h2>
	{#if events.length}
		<!-- The list is positioned inside this box, so its rows do not make the card taller than its neighbours. -->
		<div class="relative min-h-[20rem] flex-1" bind:clientHeight={boxHeight}>
		<ul class="absolute inset-0 flex flex-col divide-y divide-white/10 overflow-hidden">
			{#each shown as e (e.id)}
				<li>
					<a class="flex items-center gap-4 py-2.5 hover:bg-white/5" href={eventHref(e.id)}>
						<div class="w-10 shrink-0 text-center">
							<div class="font-display text-2xl leading-none font-extrabold">{part(e.start, { day: '2-digit' })}</div>
							<div class="mt-1 font-mono text-[10px] tracking-[.08em] text-on-dark uppercase">{part(e.start, { weekday: 'short' })}</div>
						</div>
						<div class="min-w-0 flex-1">
							<p class="flex items-center gap-2 truncate font-semibold"><span class="size-2 shrink-0 rounded-full {kindStyle(e.kind).dot}" title={t(kindLabelKey(e.kind))}></span><span class="truncate">{e.title}</span></p>
							<p class="truncate font-mono text-xs text-on-dark">{part(e.start, { hour: '2-digit', minute: '2-digit', hour12: false })}{#if e.location} · {e.location.name}{/if}{#if e.attendance.total} · {t('overview.upcoming.in', { n: e.attendance.present + e.attendance.selected, total: e.attendance.total })}{/if}</p>
						</div>
						{#if pill(e)}<span class="badge badge-sm shrink-0 border-white/70 bg-transparent text-white">{pill(e)}</span>{/if}
						<span class="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-white/30 text-white/80"><ChevronRight size={16} aria-hidden="true" /></span>
					</a>
				</li>
			{/each}
		</ul>
		</div>
	{:else}
		<p class="flex-1 py-6 text-center text-sm text-on-dark">{t('overview.upcoming.empty')}</p>
	{/if}
	<a class="mt-2 inline-flex items-center gap-1 font-mono text-xs text-on-dark hover:text-white" href="/calendar">{t('overview.upcoming.calendar')} <ArrowRight size={12} aria-hidden="true" /></a>
</div>
