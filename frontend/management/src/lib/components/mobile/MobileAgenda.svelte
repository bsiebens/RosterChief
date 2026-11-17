<script lang="ts">
	import { CalendarDays, List } from '@lucide/svelte';
	import { formatTime } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { CalendarEntry } from '#lib/portal.ts';
	import { statusPill } from './pills.ts';

	/**
	 * The Agenda on a phone, as on the design boards: kind chips (all, games, training, crew), the coming days as sections with a mono date, one
	 * row per child and event (start time and child in a tile, title, meet-up time and place, how many are going) with the status pill, and a
	 * button above the tab bar for the month view. Tapping a row opens the event, where the answer is given.
	 */
	let { entries, href, people, firstName, kind = $bindable('all'), switchHref, switchMonth = true, chips = true, past = false }: {
		entries: CalendarEntry[];
		href: (eventId: string) => string;
		people: { id: string }[];
		firstName: (memberId: string) => string;
		kind: string;
		/** Where the button at the bottom leads (month view, or back to the list). */
		switchHref: string;
		switchMonth?: boolean;
		/** The kind chips (the list view); the month view has its own legend. */
		chips?: boolean;
		/** Also show days before today (a month or a picked day). */
		past?: boolean;
	} = $props();

	const KINDS = [
		['all', 'portal.kind_all'],
		['game', 'portal.kind_games'],
		['training', 'portal.kind_training'],
		['crew', 'portal.kind_crew']
	] as const;
	const key = (date: Date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
	const todayKey = key(new Date());

	// One row per event: everyone in the family who is part of it answers on the same row, each with their own first name in full and status.
	type Who = { name: string; pill: { cls: string; text: string } };
	type Row = { id: string; start: string; title: string; kind: string; line: string; going: number | null | undefined; who: Who[]; eventId: string; cancelled: boolean };
	const rows = $derived.by(() => {
		const byKey = new Map<string, Row>();
		const add = (rowKey: string, base: Omit<Row, 'id' | 'who'>, who: Who) => {
			const found = byKey.get(rowKey);
			if (found) found.who.push(who);
			else byKey.set(rowKey, { ...base, id: rowKey, who: [who] });
		};
		for (const { event, attendance, officiating } of entries) {
			if (!past && key(new Date(event.start)) < todayKey) continue;
			const meet = event.gathering ? t('portal.meet_time', { time: formatTime(event.gathering) }) : '';
			const base = { start: event.start, title: event.title, kind: event.kind, line: [meet, event.location].filter(Boolean).join(' · '), going: undefined as number | null | undefined, eventId: event.event_id, cancelled: event.cancelled };
			for (const row of attendance) add(event.event_id, { ...base, going: row.going_count }, { name: firstName(row.member_id), pill: statusPill(row.status) });
			for (const offer of officiating) add(`${event.event_id}-official`, { ...base, kind: 'crew', title: `${event.title} · ${t(`portal.crew_${offer.role}`)}` }, { name: firstName(offer.member_id), pill: statusPill(offer.status === 'invited' ? 'no_response' : 'duty') });
		}
		return [...byKey.values()].filter((row) => kind === 'all' || row.kind === kind).sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
	});
	const groups = $derived.by(() => {
		const map = new Map<string, Row[]>();
		for (const row of rows) map.set(key(new Date(row.start)), [...(map.get(key(new Date(row.start))) ?? []), row]);
		return [...map.entries()];
	});
	const longDay = (iso: string) => new Intl.DateTimeFormat(getLang(), { weekday: 'short', day: 'numeric', month: 'short' }).format(new Date(`${iso}T12:00:00`));
</script>

<div class="grid min-w-0 gap-4">
	{#if chips}<div class="flex gap-2 overflow-x-auto" role="group" aria-label={t('portal.filter_kind')}>
		{#each KINDS as [value, label] (value)}
			<button type="button" class="inline-flex h-[38px] shrink-0 items-center rounded-full border px-3.5 text-[15px] font-semibold {kind === value ? 'border-ink bg-ink text-paper' : 'border-line bg-(--portal-panel) text-ink'}" aria-pressed={kind === value} onclick={() => (kind = value)}>{t(label)}</button>
		{/each}
	</div>{/if}

	{#each groups as [day, list] (day)}
		<section class="grid gap-2">
			<h2 class="flex items-baseline gap-2 px-0.5 font-display text-xl font-extrabold tracking-[.02em] text-ink uppercase">
				{day === todayKey ? t('portal.go_today') : longDay(day)}{#if day === todayKey}<span class="ml-auto font-mono text-xs font-normal tracking-[.04em] text-muted">{longDay(day)}</span>{/if}
			</h2>
			<div class="card divide-y divide-rule overflow-hidden">
				{#each list as row (row.id)}
					<a class="flex min-h-17 items-center gap-3 px-3.5 py-2.5 {row.cancelled ? 'opacity-60' : ''}" href={href(row.eventId)} data-sveltekit-reset="false">
						<b class="w-14 shrink-0 text-center font-display text-[1.625rem] leading-none font-extrabold text-ink">{formatTime(row.start)}</b>
						<span class="min-w-0 flex-1">
							<b class="block text-base leading-snug font-semibold text-ink {row.cancelled ? 'line-through' : ''}">{row.title}</b>
							{#if row.line}<span class="block text-sm text-muted">{row.line}</span>{/if}
							{#if row.going != null}<span class="block text-sm text-muted">{t('portal.n_going', { n: row.going })}</span>{/if}
							{#if row.who.length > 1 || (people.length > 1 && row.who.length)}
								<span class="mt-1.5 flex flex-wrap gap-x-3 gap-y-1.5">
									{#each row.who as w (w.name)}<span class="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">{w.name}<span class={w.pill.cls}>{w.pill.text}</span></span>{/each}
								</span>
							{/if}
						</span>
						{#if row.who.length === 1 && people.length <= 1}<span class={row.who[0].pill.cls}>{row.who[0].pill.text}</span>{/if}
					</a>
				{/each}
			</div>
		</section>
	{:else}
		<p class="card card-body text-sm text-muted">{t('portal.no_events')}</p>
	{/each}
</div>

<div class="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-10 -mx-3 mt-2 border-t border-line bg-paper px-4 py-2.5 sm:-mx-5">
	<a class="btn btn-outline w-full gap-2" href={switchHref}>{#if switchMonth}<CalendarDays size={16} aria-hidden="true" />{t('portal.month_view')}{:else}<List size={16} aria-hidden="true" />{t('portal.schedule')}{/if}</a>
</div>
