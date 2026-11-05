<script lang="ts">
	import { ArrowRight, ChevronRight } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { eventHref, type EventRow } from '#lib/events/types.ts';

	/** The next events on the calendar (real data), soonest first. */
	let { events }: { events: EventRow[] } = $props();
	const part = (iso: string, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(getLang(), opts).format(new Date(iso));
	const pill = (e: EventRow) => (e.club_wide ? t('overview.upcoming.club') : (e.teams[0]?.name ?? e.groups[0]?.name ?? ''));
</script>

<div class="card card-body h-full gap-1 border-ink bg-ink text-white">
	<h2 class="mb-2 font-display text-lg font-extrabold tracking-[.04em] uppercase">{t('overview.upcoming.title')}</h2>
	{#if events.length}
		<ul class="flex flex-1 flex-col divide-y divide-white/10">
			{#each events as e (e.id)}
				<li class="flex-1">
					<a class="flex h-full items-center gap-4 py-3 hover:bg-white/5" href={eventHref(e.id)}>
						<div class="w-10 shrink-0 text-center">
							<div class="font-display text-2xl leading-none font-extrabold">{part(e.start, { day: '2-digit' })}</div>
							<div class="mt-1 font-mono text-[10px] tracking-[.08em] text-on-dark uppercase">{part(e.start, { weekday: 'short' })}</div>
						</div>
						<div class="min-w-0 flex-1">
							<p class="truncate font-semibold">{e.title}</p>
							<p class="font-mono text-xs text-on-dark">{part(e.start, { hour: '2-digit', minute: '2-digit', hour12: false })}{#if e.location} · {e.location.name}{/if}</p>
						</div>
						{#if pill(e)}<span class="badge badge-sm shrink-0 border-white/70 bg-transparent text-white">{pill(e)}</span>{/if}
						<span class="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-white/30 text-white/80"><ChevronRight size={16} aria-hidden="true" /></span>
					</a>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="flex-1 py-6 text-center text-sm text-on-dark">{t('overview.upcoming.empty')}</p>
	{/if}
	<a class="mt-2 inline-flex items-center gap-1 font-mono text-xs text-on-dark hover:text-white" href="/calendar">{t('overview.upcoming.calendar')} <ArrowRight size={12} aria-hidden="true" /></a>
</div>
