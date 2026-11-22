<script lang="ts">
	import { page } from '$app/state';
	import { t } from '#lib/i18n.svelte.ts';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import { fmtDay, fromLocalInput, fmtTime, type MatchDoc } from '#lib/tournaments.svelte.ts';
	import type { BoardData } from '../../../routes/(app)/tournaments-admin/[id]/schedule/+page.server.ts';

	/**
	 * One column per field and day. Matches are positioned by minute (`px_per_minute`); drag a match onto any column to place it at the
	 * dropped time (snapped to 5 minutes), or click it to type the field and time by hand. Gaps show the break rule that made them.
	 */
	let { board, canOperate, busy, onmove, onedit }: { board: BoardData; canOperate: boolean; busy: boolean; onmove: (match: MatchDoc, fieldId: string, startsAt: string) => void; onedit: (match: MatchDoc) => void } = $props();

	/** The board gives each day's window as a timestamp; the wall-clock part is what the board needs ("2026-11-28T09:00:00+01:00" -> "09:00"). */
	const clock = (v: string) => (/^\d{2}:\d{2}/.test(v) ? v.slice(0, 5) : v.slice(11, 16));

	/** The club's crest sits at centre ice of every rink. */
	const branding = $derived((page.data as { branding?: { name?: string; logo_url?: string | null } }).branding);
	/** A full rink is only drawn for ice hockey; every other sport gets a plain playing area. */
	const rink = $derived(/hockey/i.test(`${board.sport?.slug ?? ''} ${board.sport?.name ?? ''}`));
	/** Room above the first and below the last minute, so a game never touches the rounded corners; the rink keeps its 85 x 200 ft proportions. */
	const PAD = 28;
	const RATIO = 85 / 200;

	let dragging = $state<MatchDoc | null>(null);
	let hover = $state<string>('');
	const toMin = (hhmm: string) => {
		const [h, m] = hhmm.split(':').map(Number);
		return h * 60 + m;
	};
	const hhmm = (min: number) => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
	const marks = (start: string, end: string) => {
		const out: { label: string; min: number }[] = [];
		for (let m = Math.ceil(toMin(start) / 60) * 60; m <= toMin(end); m += 60) out.push({ label: hhmm(m), min: m - toMin(start) });
		return out;
	};

	function drop(e: DragEvent, fieldId: string, date: string, start: string) {
		e.preventDefault();
		hover = '';
		const m = dragging;
		dragging = null;
		if (!m || busy) return;
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const minutes = Math.max(0, Math.round((e.clientY - rect.top) / board.px_per_minute / 5) * 5);
		const at = toMin(start) + minutes;
		onmove(m, fieldId, fromLocalInput(`${date}T${hhmm(at)}`));
	}
</script>

<div class="grid gap-8">
	{#each board.fields as f (f.field.id)}
		<section class="grid gap-3">
			<h3 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{f.field.name}</h3>
			{#if f.rules.length}<p class="text-xs text-muted">{f.rules.map((r) => r.summary).join(' · ')}</p>{/if}
			<div class="grid gap-x-4 gap-y-5 overflow-x-auto pb-2 [grid-template-columns:repeat(auto-fit,minmax(15rem,1fr))]">
				{#each f.days as day (day.date)}
					<div class="min-w-0 pl-12">
						<p class="mb-1 font-mono text-xs text-muted">{fmtDay(day.date)} · {clock(day.start)}–{clock(day.end)}</p>
						<div class="relative mx-auto" style:width="min(100%, {(day.height_px + PAD * 2) * RATIO}px)" style:height="{day.height_px + PAD * 2}px">
							{#if rink}
								<svg class="pointer-events-none absolute inset-0 size-full" viewBox="0 0 85 200" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
									<rect x="0.6" y="0.6" width="83.8" height="198.8" rx="15" fill="#fff" stroke="#8c93a1" stroke-width="1.2" />
									<g fill="none" stroke-width="0.7">
										<g stroke="#d13b3b" opacity=".55">
											<line x1="0.6" x2="84.4" y1="11" y2="11" /><line x1="0.6" x2="84.4" y1="189" y2="189" />
											<line x1="0.6" x2="84.4" y1="100" y2="100" stroke-width="1.4" stroke-dasharray="3 1" />
											<circle cx="42.5" cy="100" r="15" />
											{#each [[20.5, 31], [64.5, 31], [20.5, 169], [64.5, 169]] as [cx, cy] (`${cx}-${cy}`)}
												<circle {cx} {cy} r="15" />
												<line x1={cx - 2} x2={cx - 2} y1={cy - 15} y2={cy - 17.5} /><line x1={cx + 2} x2={cx + 2} y1={cy - 15} y2={cy - 17.5} />
												<line x1={cx - 2} x2={cx - 2} y1={cy + 15} y2={cy + 17.5} /><line x1={cx + 2} x2={cx + 2} y1={cy + 15} y2={cy + 17.5} />
											{/each}
										</g>
										<g stroke="#2f6fb5" opacity=".55">
											<line x1="0.6" x2="84.4" y1="75" y2="75" stroke-width="1.4" /><line x1="0.6" x2="84.4" y1="125" y2="125" stroke-width="1.4" />
										</g>
										<g fill="#d13b3b" stroke="none" opacity=".55">
											{#each [[20.5, 31], [64.5, 31], [20.5, 169], [64.5, 169]] as [cx, cy] (`d${cx}-${cy}`)}<circle {cx} {cy} r="1" />{/each}
											{#each [[20.5, 80], [64.5, 80], [20.5, 120], [64.5, 120]] as [cx, cy] (`n${cx}-${cy}`)}<circle {cx} {cy} r=".9" />{/each}
										</g>
										<g stroke="#2f6fb5" fill="#9ac4ee" fill-opacity=".35" opacity=".7">
											<path d="M 36.5 11 A 6 6 0 0 0 48.5 11 Z" /><path d="M 36.5 189 A 6 6 0 0 1 48.5 189 Z" />
											<rect x="39.5" y="7.6" width="6" height="3.4" fill="#fff" fill-opacity=".8" /><rect x="39.5" y="189" width="6" height="3.4" fill="#fff" fill-opacity=".8" />
										</g>
									</g>
								</svg>
							{/if}
							{#each marks(clock(day.start), clock(day.end)) as m (m.label)}
								<span class="pointer-events-none absolute -left-12 w-10 text-right font-mono text-xs font-semibold text-ink/75" style:top="{(rink ? PAD : 0) + m.min * board.px_per_minute - 8}px">{m.label}</span>
								<span class="pointer-events-none absolute inset-x-[6%] border-t border-dashed border-ink/20" style:top="{(rink ? PAD : 0) + m.min * board.px_per_minute}px"></span>
							{/each}
							{#if rink && branding}
								<span class="pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-25" aria-hidden="true"><ClubCrest name={branding.name ?? ''} logoUrl={branding.logo_url ?? null} size={72} /></span>
							{/if}
							<div
								role="application"
								aria-label={t('tn.sched.column', { field: f.field.name, day: fmtDay(day.date) })}
								class="absolute inset-x-[12%] {rink ? '' : 'rounded-lg border-2 border-ink/25 bg-white'} {hover === f.field.id + day.date ? 'outline-2 outline-primary' : ''}"
								style:top="{rink ? PAD : 0}px"
								style:bottom="{rink ? PAD : 0}px"
								ondragover={(e) => {
									if (!canOperate) return;
									e.preventDefault();
									hover = f.field.id + day.date;
								}}
								ondragleave={() => (hover = '')}
								ondrop={(e) => canOperate && drop(e, f.field.id, day.date, clock(day.start))}>
								{#if !day.slots.some((x) => x.type === 'match')}
									<p class="pointer-events-none absolute inset-x-2 top-1/2 -translate-y-1/2 rounded bg-white/90 px-2 py-1 text-center text-xs font-medium text-ink/70">{t('tn.sched.day_empty')}</p>
								{/if}
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
										style:top="{s.top_px}px"
										style:height="{s.height_px}px"
										title="{m.home_display} – {m.away_display}">
										<span class="block truncate font-semibold text-ink">{m.home_display} – {m.away_display}</span>
										<span class="block truncate font-mono text-muted">{fmtTime(m.scheduled_start)} · {m.round_label}{m.leg ? ` · ${t('tn.leg', { n: m.leg })}` : ''}</span>
									</button>
								{:else if day.slots.some((x) => x.type === 'match')}
									<div class="pointer-events-none absolute inset-x-0 flex items-center justify-center overflow-hidden rounded bg-ink/5 text-[11px] font-medium text-ink/70" style:top="{s.top_px}px" style:height="{s.height_px}px">
										{#if s.height_px >= 16}{s.rule?.summary ?? t('tn.sched.gap', { n: s.minutes })}{/if}
									</div>
								{/if}
							{/each}
							</div>
						</div>
					</div>
				{:else}
					<p class="text-sm text-muted">{t('tn.sched.no_days')}</p>
				{/each}
			</div>
		</section>
	{:else}
		<p class="text-sm text-muted">{t('tn.sched.no_fields')}</p>
	{/each}
</div>
