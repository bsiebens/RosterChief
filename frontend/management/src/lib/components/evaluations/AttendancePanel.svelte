<script lang="ts">
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { Attendance, AttendanceCounts } from '#lib/evaluations.ts';

	/** This season's attendance of one player: overall, split into games and practices (turnout, a split bar, the counts), and the last events as coloured squares. */
	let { att }: { att: Attendance } = $props();
	const day = (iso: string) => new Intl.DateTimeFormat(getLang(), { dateStyle: 'medium' }).format(new Date(iso));
	const tone = (rate: number | null) => (rate === null ? 'text-dim' : rate >= 80 ? 'text-ok-text' : rate >= 60 ? 'text-warn-text' : 'text-danger');
	const square: Record<string, string> = { present: 'bg-ok', absent: 'bg-danger', excused: 'bg-warn', no_reply: 'bg-dim', upcoming: 'border border-dashed border-dim bg-white' };
	const groups = $derived([
		{ key: 'games', label: t('eval.att.games'), c: att.games },
		{ key: 'practices', label: t('eval.att.practices'), c: att.practices }
	] as { key: string; label: string; c: AttendanceCounts }[]);
	/** Present out of every past event, answered or not: the stricter number, next to the turnout of those who answered. */
	const ofAll = (c: AttendanceCounts) => (c.total ? Math.round((c.present / c.total) * 100) : null);
	/** The share of past events nobody answered for (0 when there are none): above 5% it is shown in red. */
	const noReplyPct = (c: AttendanceCounts) => (c.total ? Math.round((c.no_reply / c.total) * 100) : 0);
	const legend = ['present', 'absent', 'excused', 'no_reply', 'upcoming'] as const;
</script>

{#snippet split(c: AttendanceCounts)}
	{#if c.total}
		<div class="flex h-2 overflow-hidden rounded-full bg-rule" role="img" aria-label={t('eval.att.split', { present: c.present, absent: c.absent, none: c.no_reply })}>
			<div class="bg-ok" style="width: {(c.present / c.total) * 100}%"></div>
			<div class="bg-danger" style="width: {(c.absent / c.total) * 100}%"></div>
			<div class="bg-warn" style="width: {(c.excused / c.total) * 100}%"></div>
			<div class="bg-dim" style="width: {(c.no_reply / c.total) * 100}%"></div>
		</div>
	{/if}
{/snippet}

{#snippet counts(c: AttendanceCounts)}
	<dl class="flex flex-wrap gap-x-4 gap-y-0.5 text-sm">
		<div class="flex items-baseline gap-1.5"><dt class="text-xs text-muted">{t('eval.att.present')}</dt><dd class="font-mono font-semibold text-ok-text">{c.present}</dd></div>
		<div class="flex items-baseline gap-1.5"><dt class="text-xs text-muted">{t('eval.att.absent')}</dt><dd class="font-mono font-semibold {c.absent ? 'text-danger' : 'text-ink'}">{c.absent}</dd></div>
		{#if c.excused}<div class="flex items-baseline gap-1.5"><dt class="text-xs text-muted">{t('eval.att.excused')}</dt><dd class="font-mono font-semibold text-warn-text">{c.excused}</dd></div>{/if}
		<div class="flex items-baseline gap-1.5"><dt class="text-xs text-muted">{t('eval.att.no_reply')}</dt><dd class="font-mono font-semibold {noReplyPct(c) > 5 ? 'text-danger' : 'text-ink'}">{c.no_reply}<span class="ml-1 text-xs font-normal">({noReplyPct(c)}%)</span></dd></div>
		{#if c.no_shows}<div class="flex items-baseline gap-1.5"><dt class="text-xs text-muted">{t('eval.att.no_shows')}</dt><dd class="font-mono font-semibold text-danger">{c.no_shows}</dd></div>{/if}
	</dl>
{/snippet}

<section class="grid gap-4 border-t border-rule pt-4" aria-label={t('eval.att.title')}>
	<div class="flex flex-wrap items-end justify-between gap-x-8 gap-y-2">
		<div>
			<p class="font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('eval.att.title')}{#if att.season} · {att.season.name}{/if}</p>
			<div class="mt-1 flex flex-wrap items-start gap-x-8 gap-y-2">
				<div title={t('eval.att.turnout_help')}>
					<p class="font-display text-4xl leading-none font-extrabold {tone(att.rate)}">{att.rate === null ? '–' : `${att.rate}%`}</p>
					<p class="mt-1 text-xs text-muted">{t('eval.att.turnout')}</p>
				</div>
				<div title={t('eval.att.of_all_help')}>
					<p class="font-display text-4xl leading-none font-extrabold {tone(ofAll(att))}">{ofAll(att) === null ? '–' : `${ofAll(att)}%`}</p>
					<p class="mt-1 text-xs text-muted">{t('eval.att.of_all')}</p>
				</div>
			</div>
		</div>
		{@render counts(att)}
	</div>
	{@render split(att)}

	<div class="grid gap-4 sm:grid-cols-2">
		{#each groups as g (g.key)}
			<div class="grid gap-2 rounded-lg border border-line p-3">
				<div class="flex items-baseline justify-between gap-2">
					<h4 class="font-display text-xs font-bold tracking-[.08em] text-ink uppercase">{g.label}<span class="ml-2 font-mono text-xs font-normal text-muted">{g.c.total}</span></h4>
					<span class="flex gap-4 text-right">
						<span title={t('eval.att.turnout_help')}><span class="block font-display text-xl leading-none font-extrabold {tone(g.c.rate)}">{g.c.rate === null ? '–' : `${g.c.rate}%`}</span><span class="block text-[11px] text-muted">{t('eval.att.turnout_short')}</span></span>
						<span title={t('eval.att.of_all_help')}><span class="block font-display text-xl leading-none font-extrabold {tone(ofAll(g.c))}">{ofAll(g.c) === null ? '–' : `${ofAll(g.c)}%`}</span><span class="block text-[11px] text-muted">{t('eval.att.of_all_short')}</span></span>
					</span>
				</div>
				{@render split(g.c)}
				{@render counts(g.c)}
			</div>
		{/each}
	</div>

	{#if att.recent.length}
		<div>
			<p class="mb-1.5 text-xs text-muted">{t('eval.att.recent', { n: att.recent_limit })} <span class="text-dim">· {t('eval.att.recent_n', { n: att.recent.length })}</span></p>
			<ul class="flex flex-wrap gap-1">
				{#each att.recent as b, i (i)}
					<li class="size-4 rounded-sm {square[b.state]}" title="{day(b.date)} · {b.title} · {t(`eval.att.state.${b.state}`)}"></li>
				{/each}
			</ul>
			<ul class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
				{#each legend as k (k)}<li class="flex items-center gap-1.5"><span class="size-2.5 rounded-sm {square[k]}"></span>{t(`eval.att.state.${k}`)}</li>{/each}
			</ul>
		</div>
	{/if}
</section>
