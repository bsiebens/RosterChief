<script lang="ts">
	import { ArrowRight, ChevronRight } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { mockUpcoming } from '#lib/overview.ts';

	/** Sample fixtures until the calendar module exists. */
	const items = mockUpcoming();
	const part = (iso: string, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(getLang(), opts).format(new Date(iso));
</script>

<div class="card card-body sample-stripes-dark h-full gap-1 border-ink bg-ink text-white" title={t('overview.sample_hint')}>
	<h2 class="mb-2 font-display text-lg font-extrabold tracking-[.04em] uppercase">{t('overview.upcoming.title')}</h2>
	<ul class="flex flex-1 flex-col divide-y divide-white/10">
		{#each items as e (e.id)}
			<li class="flex flex-1 items-center gap-4 py-3">
				<div class="w-10 shrink-0 text-center">
					<div class="font-display text-2xl leading-none font-extrabold">{part(e.start, { day: '2-digit' })}</div>
					<div class="mt-1 font-mono text-[10px] tracking-[.08em] text-on-dark uppercase">{part(e.start, { weekday: 'short' })}</div>
				</div>
				<div class="min-w-0 flex-1">
					<p class="truncate font-semibold">{e.title}</p>
					<p class="font-mono text-xs text-on-dark">{part(e.start, { hour: '2-digit', minute: '2-digit', hour12: false })}</p>
				</div>
				<span class="badge badge-sm shrink-0 border-white/70 bg-transparent text-white">{e.team}</span>
				<span class="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-white/30 text-white/80"><ChevronRight size={16} aria-hidden="true" /></span>
			</li>
		{/each}
	</ul>
	<span class="mt-2 inline-flex items-center gap-1 font-mono text-xs text-on-dark">{t('overview.upcoming.calendar')} <ArrowRight size={12} aria-hidden="true" /></span>
</div>
