<script lang="ts">
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';
	import { formatTime } from '#lib/events/types.ts';
	import { dateKey, goingShare, isGame, startOfToday } from '#lib/coach.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { CoachEvent } from '#lib/portal.ts';
	import PortalEventDate from '#lib/components/PortalEventDate.svelte';

	let { events }: { events: CoachEvent[] } = $props();
	let filter = $state<'all' | 'game' | 'training'>('all');
	let month = $state(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
	let picked = $state('');
	const FILTERS = [['all', 'portal.all'], ['game', 'coach.games'], ['training', 'events.kind.training']] as const;
	const shown = $derived(events.filter((event) => filter === 'all' || (filter === 'game' ? isGame(event) : event.kind === 'training')));
	const byDay = $derived.by(() => {
		const map = new Map<string, CoachEvent[]>();
		for (const event of shown) {
			const key = dateKey(new Date(event.start));
			map.set(key, [...(map.get(key) ?? []), event]);
		}
		return map;
	});
	const monthLabel = $derived(new Intl.DateTimeFormat(getLang(), { month: 'long', year: 'numeric' }).format(month));
	const weekdays = $derived(Array.from({ length: 7 }, (_, i) => new Intl.DateTimeFormat(getLang(), { weekday: 'short' }).format(new Date(2024, 0, 1 + i))));
	const cells = $derived.by(() => {
		const first = new Date(month.getFullYear(), month.getMonth(), 1);
		const lead = (first.getDay() + 6) % 7;
		const count = Math.ceil((lead + new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()) / 7) * 7;
		return Array.from({ length: count }, (_, i) => new Date(month.getFullYear(), month.getMonth(), 1 - lead + i));
	});
	const todayKey = dateKey(new Date());
	const list = $derived(shown.filter((event) => (picked ? dateKey(new Date(event.start)) === picked : Date.parse(event.start) >= startOfToday().getTime())));
	const step = (by: number) => (month = new Date(month.getFullYear(), month.getMonth() + by, 1));
	const href = (event: CoachEvent) => (event.can_manage_attendance ? `/coach/attendance/${event.id}` : `/calendar/events/${event.id}?view=coach`);
</script>

<div class="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:items-start">
	<div class="grid min-w-0 gap-3">
		<div class="flex flex-wrap gap-1.5" role="group" aria-label={t('coach.filter_events')}>
			{#each FILTERS as [key, label] (key)}<button type="button" class="portal-person compact text" aria-pressed={filter === key} onclick={() => (filter = key)}>{t(label)}</button>{/each}
		</div>
		<section class="card p-3">
			<div class="mb-2 flex items-center justify-between gap-2">
				<button type="button" class="btn btn-ghost btn-square btn-sm" aria-label={t('coach.month_prev')} onclick={() => step(-1)}><ChevronLeft size={16} aria-hidden="true" /></button>
				<h2 class="font-display text-sm font-extrabold tracking-wider text-ink uppercase">{monthLabel}</h2>
				<button type="button" class="btn btn-ghost btn-square btn-sm" aria-label={t('coach.month_next')} onclick={() => step(1)}><ChevronRight size={16} aria-hidden="true" /></button>
			</div>
			<div class="grid grid-cols-7 gap-0.5 text-center">
				{#each weekdays as name (name)}<b class="py-1 font-mono text-[10px] font-medium tracking-wider text-muted uppercase">{name}</b>{/each}
				{#each cells as day (day.getTime())}
					{@const key = dateKey(day)}
					{@const dayEvents = byDay.get(key) ?? []}
					{@const outside = day.getMonth() !== month.getMonth()}
					<button
						type="button"
						class="relative grid h-9 place-items-center rounded-lg text-[13px] font-semibold {key === todayKey ? 'bg-ink text-(--color-white)' : picked === key ? 'bg-subhead ring-2 ring-club' : 'text-ink hover:bg-subhead'} {outside ? 'opacity-40' : ''}"
						disabled={!dayEvents.length}
						aria-pressed={picked === key}
						aria-label={dayEvents.length ? `${day.getDate()} · ${dayEvents.map((event) => event.title).join(', ')}` : String(day.getDate())}
						onclick={() => (picked = picked === key ? '' : key)}
					>
						{day.getDate()}
						{#if dayEvents.length}<span class="absolute bottom-0.5 size-1.5 rounded-full {dayEvents.some(isGame) ? 'bg-club' : 'bg-info-text'}"></span>{/if}
					</button>
				{/each}
			</div>
		</section>
	</div>

	<section class="card min-w-0">
		{#if picked}<div class="flex justify-end border-b border-rule px-3 py-1"><button type="button" class="link-more" onclick={() => (picked = '')}>{t('coach.day_clear')}</button></div>{/if}
		<div class="divide-y divide-rule">
			{#each list as event (event.id)}
				<a class="flex min-h-15 items-center gap-3 px-3.5 py-2.5 hover:bg-subhead {isGame(event) ? 'shadow-[inset_3px_0_0_var(--color-club)]' : ''}" href={href(event)}>
					<PortalEventDate start={event.start} compact />
					<span class="min-w-0 flex-1">
						<b class="block truncate text-sm text-ink">{event.title}</b>
						<span class="block truncate text-xs text-muted">{formatTime(event.start)}{event.location ? ' · ' + event.location : ''}</span>
					</span>
					{#if event.cancelled}
						<span class="badge badge-sm badge-error">{t('portal.cancelled')}</span>
					{:else}
						{#if event.lineup && !event.lineup_published && Date.parse(event.start) >= Date.now()}<span class="badge badge-sm badge-warning">{t('portal.lineup')}</span>{/if}
						{#if event.can_manage_attendance && event.invited > 0}
							<span class="grid shrink-0 justify-items-end gap-1" title={t('coach.attendance_of', { going: event.going, invited: event.invited })}>
								<span class="font-mono text-sm font-semibold text-ink">{event.going}/{event.invited}</span>
								<span class="block h-1.5 w-14 overflow-hidden rounded-full bg-rule"><i class="block h-full rounded-full bg-club" style:width="{goingShare(event)}%"></i></span>
							</span>
						{/if}
					{/if}
				</a>
			{:else}
				<p class="px-4 py-3 text-sm text-muted">{t('portal.no_events')}</p>
			{/each}
		</div>
	</section>
</div>
