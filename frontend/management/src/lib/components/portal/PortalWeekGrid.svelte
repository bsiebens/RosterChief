<script lang="ts">
	import { formatLongDay, formatTime, kindStyle, statusLabelKey, statusStyle } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { CalendarEntry } from '#lib/portal.ts';

	/**
	 * The week view of the member calendar, laid out like the manage app's: a column per day, a row per hour from 06:00, events placed by their
	 * start and length (side by side when they overlap), and a line for the current time. Each event shows the family's answer; a click opens it.
	 */
	let { days, entries, href, firstName, many }: { days: Date[]; entries: CalendarEntry[]; href: (eventId: string) => string; firstName: (memberId: string) => string; many: boolean } = $props();

	const HOUR_PX = 56;
	const MIN_BLOCK_PX = 30;
	const START = 6;
	const END = 24;
	const hours = Array.from({ length: END - START }, (_, i) => START + i);
	const key = (d: Date) => [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
	const today = new Date();
	const weekday = (d: Date) => new Intl.DateTimeFormat(getLang(), { weekday: 'short' }).format(d);

	type Seg = { entry: CalendarEntry; from: number; to: number; lane: number; lanes: number; top: number; height: number };
	function layout(day: Date): Seg[] {
		const dayStart = new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
		const items = entries
			.filter((entry) => key(new Date(entry.event.start)) === key(day))
			.map((entry) => {
				const s = new Date(entry.event.start).getTime();
				const e = entry.event.end ? new Date(entry.event.end).getTime() : s + 3_600_000;
				const from = Math.round((s - dayStart) / 60000);
				return { entry, from, to: Math.max(Math.round((Math.min(e, dayStart + 86_400_000) - dayStart) / 60000), from + 30) };
			})
			.sort((a, b) => a.from - b.from || b.to - a.to);
		const out: Seg[] = [];
		let cluster: Seg[] = [];
		let clusterEnd = -1;
		const close = () => {
			const lanes = Math.max(1, ...cluster.map((c) => c.lane + 1));
			for (const c of cluster) c.lanes = lanes;
			cluster = [];
		};
		for (const it of items) {
			if (it.from >= clusterEnd && cluster.length) close();
			const used = new Set(cluster.filter((c) => c.to > it.from).map((c) => c.lane));
			let lane = 0;
			while (used.has(lane)) lane++;
			const seg: Seg = { ...it, lane, lanes: 1, top: ((it.from - START * 60) / 60) * HOUR_PX, height: Math.max(((it.to - it.from) / 60) * HOUR_PX, MIN_BLOCK_PX) };
			cluster.push(seg);
			out.push(seg);
			clusterEnd = Math.max(clusterEnd, it.to);
		}
		if (cluster.length) close();
		return out;
	}
	const nowTop = ((today.getHours() * 60 + today.getMinutes() - START * 60) / 60) * HOUR_PX;
</script>

<div class="card overflow-hidden">
	<div class="grid grid-cols-[3.5rem_repeat(7,minmax(0,1fr))] border-b border-line bg-subhead">
		<div></div>
		{#each days as day (key(day))}
			{@const isToday = key(day) === key(today)}
			<div class="border-l border-line px-2 py-2 text-center" aria-current={isToday ? 'date' : undefined}>
				<span class="font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{weekday(day)}</span>
				<span class="ml-1 inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1 font-mono text-xs {isToday ? 'bg-club font-bold text-club-content' : 'text-ink'}">{day.getDate()}</span>
			</div>
		{/each}
	</div>
	<div class="grid grid-cols-[3.5rem_repeat(7,minmax(0,1fr))]">
		<div class="relative" style="height: {hours.length * HOUR_PX}px" aria-hidden="true">
			{#each hours as h, i (h)}<span class="absolute right-2 font-mono text-[11px] text-dim" style="top: {i * HOUR_PX}px; {i === 0 ? '' : 'transform: translateY(-50%)'}">{String(h).padStart(2, '0')}:00</span>{/each}
		</div>
		{#each days as day (key(day))}
			{@const isToday = key(day) === key(today)}
			<div class="relative border-l border-line {isToday ? 'bg-club/[0.03]' : ''}" style="height: {hours.length * HOUR_PX}px; background-image: repeating-linear-gradient(to bottom, transparent 0, transparent {HOUR_PX - 1}px, var(--color-line, #e5e7eb) {HOUR_PX - 1}px, var(--color-line, #e5e7eb) {HOUR_PX}px)" aria-label={formatLongDay(day)}>
				{#each layout(day) as seg (seg.entry.event.event_id)}
					{@const event = seg.entry.event}
					<a
						href={href(event.event_id)}
						data-sveltekit-reset="false"
						class="absolute overflow-hidden rounded border border-l-4 border-line bg-white px-1.5 py-1 text-xs shadow-sm hover:z-10 hover:shadow-md focus:z-10 {kindStyle(event.kind).bar} {event.cancelled ? 'bg-subhead opacity-70' : ''}"
						style="top: {seg.top}px; height: {seg.height - 2}px; left: calc({(seg.lane / seg.lanes) * 100}% + 2px); width: calc({100 / seg.lanes}% - 4px)"
						title="{formatTime(event.start)} {event.title}">
						<span class="flex items-center justify-between gap-1 font-mono text-[10px] text-muted"><span>{formatTime(event.start)}</span>{#if event.score_for != null && event.score_against != null}<b class="font-display text-sm text-ink">{event.score_for}–{event.score_against}</b>{/if}</span>
						<span class="block truncate font-semibold text-ink {event.cancelled ? 'line-through' : ''}">{event.title}</span>
						{#if seg.height > 70}
							<span class="mt-0.5 flex flex-wrap gap-1">
								{#each seg.entry.attendance as row (row.id)}<span class="badge badge-xs border {statusStyle(row.status)}">{many ? `${firstName(row.member_id)} · ` : ''}{t(statusLabelKey(row.status))}</span>{/each}
							</span>
						{/if}
					</a>
				{/each}
				{#if isToday && nowTop >= 0 && nowTop <= hours.length * HOUR_PX}
					<div class="pointer-events-none absolute inset-x-0 z-20 border-t-2 border-club" style="top: {nowTop}px"><span class="absolute -top-1.5 -left-1 h-2.5 w-2.5 rounded-full bg-club"></span></div>
				{/if}
			</div>
		{/each}
	</div>
</div>
