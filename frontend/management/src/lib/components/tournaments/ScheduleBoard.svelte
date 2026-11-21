<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { fmtDay, fromLocalInput, fmtTime, type MatchDoc } from '#lib/tournaments.svelte.ts';
	import type { BoardData } from '../../../routes/(app)/tournaments-admin/[id]/schedule/+page.server.ts';

	/**
	 * One column per field and day. Matches are positioned by minute (`px_per_minute`); drag a match onto any column to place it at the
	 * dropped time (snapped to 5 minutes), or click it to type the field and time by hand. Gaps show the break rule that made them.
	 */
	let { board, canOperate, busy, onmove, onedit }: { board: BoardData; canOperate: boolean; busy: boolean; onmove: (match: MatchDoc, fieldId: string, startsAt: string) => void; onedit: (match: MatchDoc) => void } = $props();

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

<div class="grid gap-5">
	{#each board.fields as f (f.field.id)}
		<section class="card card-body gap-3">
			<h3 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{f.field.name}</h3>
			{#if f.rules.length}<p class="text-xs text-muted">{f.rules.map((r) => r.summary).join(' · ')}</p>{/if}
			<div class="flex gap-4 overflow-x-auto pb-2">
				{#each f.days as day (day.date)}
					<div class="w-52 shrink-0">
						<p class="mb-1 font-mono text-xs text-muted">{fmtDay(day.date)} · {day.start}–{day.end}</p>
						<div
							role="application"
							aria-label={t('tn.sched.column', { field: f.field.name, day: fmtDay(day.date) })}
							class="relative ml-9 rounded border {hover === f.field.id + day.date ? 'border-primary bg-paper' : 'border-line'}"
							style:height="{day.height_px}px"
							ondragover={(e) => {
								if (!canOperate) return;
								e.preventDefault();
								hover = f.field.id + day.date;
							}}
							ondragleave={() => (hover = '')}
							ondrop={(e) => canOperate && drop(e, f.field.id, day.date, day.start)}>
							{#each marks(day.start, day.end) as m (m.label)}
								<span class="pointer-events-none absolute -left-9 font-mono text-[10px] text-muted" style:top="{m.min * board.px_per_minute - 6}px">{m.label}</span>
								<span class="pointer-events-none absolute inset-x-0 border-t border-dashed border-line" style:top="{m.min * board.px_per_minute}px"></span>
							{/each}
							{#each day.slots as s, i (i)}
								{#if s.type === 'match' && s.match}
									{@const m = s.match}
									<button
										type="button"
										draggable={canOperate}
										ondragstart={() => (dragging = m)}
										ondragend={() => (dragging = null)}
										onclick={() => canOperate && onedit(m)}
										class="absolute inset-x-0.5 overflow-hidden rounded border border-line bg-white px-1.5 py-0.5 text-left text-[11px] leading-tight shadow-sm {canOperate ? 'cursor-grab hover:border-ink' : ''} {m.status === 'completed' ? 'opacity-70' : ''}"
										style:top="{s.top_px}px"
										style:height="{s.height_px}px"
										title="{m.home_display} – {m.away_display}">
										<span class="block truncate font-semibold text-ink">{m.home_display} – {m.away_display}</span>
										<span class="block truncate font-mono text-muted">{fmtTime(m.scheduled_start)} · {m.round_label}</span>
									</button>
								{:else}
									<div class="pointer-events-none absolute inset-x-0.5 overflow-hidden rounded bg-ink/5 px-1.5 text-[10px] text-muted italic" style:top="{s.top_px}px" style:height="{s.height_px}px">
										{s.rule?.summary ?? t('tn.sched.gap', { n: s.minutes })}
									</div>
								{/if}
							{/each}
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
