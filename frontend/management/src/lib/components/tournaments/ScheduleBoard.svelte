<script lang="ts">
	import { page } from '$app/state';
	import { MapPin } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { blockEnd, blockStart, fmtDay, fmtTime, fromLocalInput, type MatchDoc, type ScheduleBlock } from '#lib/tournaments.svelte.ts';
	import type { BoardData } from '../../../routes/(app)/tournaments-admin/[id]/schedule/+page.server.ts';

	/**
	 * Per field: the rink (or just its name) on the left, and a calendar on the right with one column per day on a shared hour axis. Games
	 * are positioned by minute (`px_per_minute`) with the idle time between them as breaks, and the schedule blocks (lunch, ceremonies)
	 * stand in their own lane. Drag a game onto any day column to place it at the dropped time (snapped to 5 minutes), or click it to type
	 * the field and time by hand.
	 */
	let { board, blocks = [], canOperate, busy, onmove, onedit }: { board: BoardData; blocks?: ScheduleBlock[]; canOperate: boolean; busy: boolean; onmove: (match: MatchDoc, fieldId: string, startsAt: string) => void; onedit: (match: MatchDoc) => void } = $props();

	/** The board gives each day's window as a timestamp; the wall-clock part is what the board needs ("2026-11-28T09:00:00+01:00" -> "09:00"). */
	const clock = (v: string) => (/^\d{2}:\d{2}/.test(v) ? v.slice(0, 5) : v.slice(11, 16));
	const toMin = (hhmm: string) => {
		const [h, m] = hhmm.split(':').map(Number);
		return h * 60 + m;
	};
	const hhmm = (min: number) => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
	const ppm = $derived(board.px_per_minute);

	const branding = $derived((page.data as { branding?: { name?: string; logo_url?: string | null } }).branding);
	/** A full rink is only drawn for ice hockey; every other sport shows the field name alone. */
	const rink = $derived(/hockey/i.test(`${board.sport?.slug ?? ''} ${board.sport?.name ?? ''}`));

	let dragging = $state<MatchDoc | null>(null);
	let hover = $state<string>('');

	/** The shared time axis of a field: from 5 minutes before the earliest opening to 5 minutes after the latest closing of its days. */
	function axis(days: { start: string; end: string }[]) {
		if (!days.length) return { start: 0, end: 0 };
		const start = Math.max(0, Math.min(...days.map((d) => toMin(clock(d.start)))) - 5);
		const end = Math.min(24 * 60, Math.max(...days.map((d) => toMin(clock(d.end)))) + 5);
		return { start, end };
	}
	const hours = (a: { start: number; end: number }) => {
		const out: number[] = [];
		for (let m = Math.ceil(a.start / 60) * 60; m <= a.end; m += 60) out.push(m);  // labelled on whole hours
		return out;
	};

	const localDate = (iso: string) => {
		const d = new Date(iso);
		return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	};
	const minuteOfDay = (iso: string) => {
		const d = new Date(iso);
		return d.getHours() * 60 + d.getMinutes();
	};
	/** The blocks of a day that touch this field (a block with no field holds every field), side by side where they overlap. */
	function dayBlocks(fieldId: string, date: string) {
		const own = blocks.filter((b) => localDate(blockStart(b)) === date && (!b.field || b.field.id === fieldId)).map((b) => ({ b, from: minuteOfDay(blockStart(b)), to: Math.max(minuteOfDay(blockEnd(b)), minuteOfDay(blockStart(b)) + 5) })).sort((x, y) => x.from - y.from);
		const lanesEnd: number[] = [];
		const placed = own.map((x) => {
			let lane = lanesEnd.findIndex((end) => end <= x.from);
			if (lane < 0) lane = lanesEnd.length;
			lanesEnd[lane] = x.to;
			return { ...x, lane };
		});
		return { items: placed, lanes: Math.max(1, lanesEnd.length) };
	}

	function drop(e: DragEvent, fieldId: string, date: string, axisStart: number) {
		e.preventDefault();
		hover = '';
		const m = dragging;
		dragging = null;
		if (!m || busy) return;
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const minutes = Math.max(0, Math.round((e.clientY - rect.top) / ppm / 5) * 5);
		onmove(m, fieldId, fromLocalInput(`${date}T${hhmm(axisStart + minutes)}`));
	}
</script>

<div class="grid gap-10">
	{#each board.fields as f (f.field.id)}
		{@const ax = axis(f.days)}
		{@const height = (ax.end - ax.start) * ppm}
		<section class="grid items-start gap-5 lg:grid-cols-[13rem_minmax(0,1fr)]">
			<figure class="grid justify-items-center gap-2">
				{#if rink}
					<div class="relative w-full max-w-[11rem]">
						<svg viewBox="0 0 85 200" class="w-full" aria-hidden="true">
							<rect x="0.6" y="0.6" width="83.8" height="198.8" rx="15" fill="#fff" stroke="#8c93a1" stroke-width="1.2" />
							<g fill="none" stroke-width="0.7">
								<g stroke="#d13b3b" opacity=".6">
									<line x1="0.6" x2="84.4" y1="11" y2="11" /><line x1="0.6" x2="84.4" y1="189" y2="189" />
									<line x1="0.6" x2="84.4" y1="100" y2="100" stroke-width="1.4" stroke-dasharray="3 1" />
									<circle cx="42.5" cy="100" r="15" />
									{#each [[20.5, 31], [64.5, 31], [20.5, 169], [64.5, 169]] as [cx, cy] (`${cx}-${cy}`)}
										<circle {cx} {cy} r="15" />
										<line x1={cx - 2} x2={cx - 2} y1={cy - 15} y2={cy - 17.5} /><line x1={cx + 2} x2={cx + 2} y1={cy - 15} y2={cy - 17.5} />
										<line x1={cx - 2} x2={cx - 2} y1={cy + 15} y2={cy + 17.5} /><line x1={cx + 2} x2={cx + 2} y1={cy + 15} y2={cy + 17.5} />
									{/each}
								</g>
								<g stroke="#2f6fb5" opacity=".6"><line x1="0.6" x2="84.4" y1="75" y2="75" stroke-width="1.4" /><line x1="0.6" x2="84.4" y1="125" y2="125" stroke-width="1.4" /></g>
								<g fill="#d13b3b" stroke="none" opacity=".6">
									{#each [[20.5, 31], [64.5, 31], [20.5, 169], [64.5, 169]] as [cx, cy] (`d${cx}-${cy}`)}<circle {cx} {cy} r="1" />{/each}
									{#each [[20.5, 80], [64.5, 80], [20.5, 120], [64.5, 120]] as [cx, cy] (`n${cx}-${cy}`)}<circle {cx} {cy} r=".9" />{/each}
								</g>
								<g stroke="#2f6fb5" fill="#9ac4ee" fill-opacity=".35" opacity=".75">
									<path d="M 36.5 11 A 6 6 0 0 0 48.5 11 Z" /><path d="M 36.5 189 A 6 6 0 0 1 48.5 189 Z" />
									<rect x="39.5" y="7.6" width="6" height="3.4" fill="#fff" fill-opacity=".8" /><rect x="39.5" y="189" width="6" height="3.4" fill="#fff" fill-opacity=".8" />
								</g>
							</g>
						</svg>
						{#if branding}
							<span class="pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-30" aria-hidden="true"><ClubCrest name={branding.name ?? ''} logoUrl={branding.logo_url ?? null} size={44} /></span>
						{/if}
					</div>
				{/if}
				<figcaption class="grid justify-items-center gap-1 text-center">
					<span class="flex items-center gap-1.5 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><MapPin size={14} class="shrink-0" aria-hidden="true" /> {f.field.name}</span>
					{#if f.rules.length}<span class="text-xs text-muted">{f.rules.map((r) => r.summary).join(' · ')}</span>{/if}
				</figcaption>
			</figure>

			<div class="overflow-x-auto pb-2">
				{#if f.days.length}
					<div class="grid min-w-max gap-x-2 [grid-template-columns:3rem_repeat(var(--days),minmax(15rem,1fr))]" style:--days={f.days.length}>
						<div></div>
						{#each f.days as day (day.date)}
							<p class="border-b border-line pb-1 text-center font-display text-xs font-bold tracking-[.08em] text-ink uppercase">{fmtDay(day.date)} <span class="font-mono font-normal text-muted">· {clock(day.start)}–{clock(day.end)}</span></p>
						{/each}

						<div class="relative" style:height="{height}px">
							{#each hours(ax) as m (m)}
								<span class="absolute right-1 -translate-y-1/2 font-mono text-xs font-semibold text-ink/75" style:top="{(m - ax.start) * ppm}px">{hhmm(m)}</span>
							{/each}
						</div>

						{#each f.days as day (day.date)}
							{@const open = toMin(clock(day.start)) - ax.start}
							{@const close = toMin(clock(day.end)) - ax.start}
							{@const lane = dayBlocks(f.field.id, day.date)}
							{@const hasMatches = day.slots.some((x) => x.type === 'match')}
							<div
								role="application"
								aria-label={t('tn.sched.column', { field: f.field.name, day: fmtDay(day.date) })}
								class="relative overflow-hidden rounded-lg border bg-white {hover === f.field.id + day.date ? 'border-primary' : 'border-line'}"
								style:height="{height}px"
								ondragover={(e) => {
									if (!canOperate) return;
									e.preventDefault();
									hover = f.field.id + day.date;
								}}
								ondragleave={() => (hover = '')}
								ondrop={(e) => canOperate && drop(e, f.field.id, day.date, ax.start)}>
								<!-- closed hours -->
								<span class="pointer-events-none absolute inset-x-0 top-0 border-b border-slate-300" style:height="{open * ppm}px" style="background-image: repeating-linear-gradient(135deg, rgb(148 163 184 / 0.45) 0 2px, transparent 2px 7px); background-color: rgb(241 245 249);"></span>
								<span class="pointer-events-none absolute inset-x-0 bottom-0 border-t border-slate-300" style:height="{Math.max(0, height - close * ppm)}px" style="background-image: repeating-linear-gradient(135deg, rgb(148 163 184 / 0.45) 0 2px, transparent 2px 7px); background-color: rgb(241 245 249);"></span>
								{#each hours(ax) as m (m)}
									<span class="pointer-events-none absolute inset-x-0 border-t border-line" style:top="{(m - ax.start) * ppm}px"></span>
								{/each}
								{#if !hasMatches}
									<p class="pointer-events-none absolute inset-x-3 top-1/2 -translate-y-1/2 text-center text-xs font-medium text-muted">{t('tn.sched.day_empty')}</p>
								{/if}

								<!-- games and breaks -->
								<div class="absolute inset-y-0 left-1.5 {lane.items.length ? 'right-[36%]' : 'right-1.5'}">
									{#each day.slots as s, i (i)}
										{#if s.type === 'match' && s.match}
											{@const m = s.match}
											<button
												type="button"
												draggable={canOperate}
												ondragstart={() => (dragging = m)}
												ondragend={() => (dragging = null)}
												onclick={() => canOperate && onedit(m)}
												class="absolute inset-x-0 overflow-hidden rounded border border-line bg-white px-1.5 py-0.5 text-left text-[11px] leading-tight shadow-sm {canOperate ? 'cursor-grab hover:border-ink' : ''} {m.status === 'completed' ? 'opacity-70' : ''}"
												style:top="{open * ppm + s.top_px}px"
												style:height="{s.height_px}px"
												title="{m.home_display} – {m.away_display}">
												<span class="block truncate font-semibold text-ink">{m.home_display} – {m.away_display}</span>
												<span class="block truncate font-mono text-muted">{fmtTime(m.scheduled_start)} · {m.round_label}{m.leg ? ` · ${t('tn.leg', { n: m.leg })}` : ''}</span>
											</button>
										{:else if hasMatches}
											<div class="pointer-events-none absolute inset-x-0 flex items-center justify-center overflow-hidden rounded bg-slate-100 text-[11px] font-medium text-ink/70" style:top="{open * ppm + s.top_px}px" style:height="{s.height_px}px">
												{#if s.height_px >= 16}{s.rule?.summary ?? t('tn.sched.gap', { n: s.minutes })}{/if}
											</div>
										{/if}
									{/each}
								</div>

								<!-- schedule blocks -->
								{#if lane.items.length}
									<div class="pointer-events-none absolute inset-y-0 right-1.5 left-[66%]">
										{#each lane.items as x (x.b.id)}
											<div
												class="absolute overflow-hidden rounded border px-1 py-0.5 text-[10px] leading-tight {x.b.kind === 'lunch' ? 'border-amber-300 bg-amber-100 text-amber-900' : 'border-sky-300 bg-sky-100 text-sky-900'}"
												style:top="{(x.from - ax.start) * ppm}px"
												style:height="{Math.max(14, (x.to - x.from) * ppm)}px"
												style:left="{(x.lane / lane.lanes) * 100}%"
												style:width="calc({100 / lane.lanes}% - 2px)"
												title="{x.b.title} {hhmm(x.from)}–{hhmm(x.to)}{x.b.teams.length ? ` · ${x.b.teams.map((tm) => tm.name).join(', ')}` : ''}">
												<span class="block truncate font-semibold">{x.b.title}</span>
												{#if x.b.teams.length}<span class="block truncate">{x.b.teams.map((tm) => tm.name).join(', ')}</span>{/if}
											</div>
										{/each}
									</div>
								{/if}
							</div>
						{/each}
					</div>
				{:else}
					<p class="text-sm text-muted">{t('tn.sched.no_days')}</p>
				{/if}
			</div>
		</section>
	{:else}
		<p class="text-sm text-muted">{t('tn.sched.no_fields')}</p>
	{/each}
</div>
