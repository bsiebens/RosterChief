<script lang="ts">
	import { CalendarCheck } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { attendanceSummary, mockSessions, mockWeeks, type SessionStatus } from '#lib/members/mock.ts';

	/** Sample content: attendance rate, a 12-week strip and the most recent sessions. */
	const sessions = mockSessions();
	const weeks = mockWeeks();
	const sum = attendanceSummary(sessions);
	const dot: Record<SessionStatus, string> = { present: 'bg-ok', excused: 'bg-warn', absent: 'bg-danger' };
	const badge: Record<SessionStatus, string> = { present: 'badge-success', excused: 'badge-warning', absent: 'badge-error' };
	const stats = $derived([
		{ key: 'present' as const, n: sum.present },
		{ key: 'excused' as const, n: sum.excused },
		{ key: 'absent' as const, n: sum.absent }
	]);
</script>

<div class="grid gap-4 lg:grid-cols-[22rem_1fr]">
	<div class="grid content-start gap-4">
		<div class="card card-body sample-stripes">
			<h2 class="card-title"><CalendarCheck size={18} /> {t('profile.att.rate')}</h2>
			<p class="font-display text-5xl font-extrabold text-ink">{sum.rate}%</p>
			<p class="text-sm text-muted">{t('profile.att.sessions', { n: sum.total })}</p>
			<div class="mt-2 flex h-2.5 overflow-hidden rounded-full bg-rule">
				{#each stats as s (s.key)}<div class="{dot[s.key]} h-full" style="width: {(s.n / sum.total) * 100}%"></div>{/each}
			</div>
			<ul class="mt-2 grid gap-1.5 text-sm">
				{#each stats as s (s.key)}
					<li class="flex items-center justify-between"><span class="inline-flex items-center gap-2 text-muted"><span class="h-2.5 w-2.5 rounded-full {dot[s.key]}"></span>{t(`profile.att.${s.key}`)}</span><span class="font-mono text-xs font-semibold text-ink">{s.n}</span></li>
				{/each}
			</ul>
		</div>
		<div class="card card-body sample-stripes">
			<h2 class="card-title text-base">{t('profile.att.weeks')}</h2>
			<div class="grid grid-cols-6 gap-2">
				{#each weeks as w, i (i)}<span class="h-8 rounded-md {dot[w]} opacity-90" title={t(`profile.att.${w}`)}></span>{/each}
			</div>
		</div>
	</div>

	<div class="card card-body sample-stripes">
		<h2 class="card-title text-base">{t('profile.att.recent')}</h2>
		<div class="overflow-x-auto">
			<table class="table">
				<thead><tr><th>{t('profile.att.col.date')}</th><th>{t('profile.att.col.session')}</th><th>{t('profile.att.col.team')}</th><th class="text-right">{t('profile.att.col.status')}</th></tr></thead>
				<tbody>
					{#each sessions as s (s.date)}
						<tr class="hover:bg-surface">
							<td class="font-semibold">{formatDate(s.date)}</td>
							<td class="text-sm">{t(`profile.att.kind.${s.kind}`)}</td>
							<td class="text-sm text-muted">{s.team}</td>
							<td class="text-right"><span class="badge badge-sm {badge[s.status]}">{t(`profile.att.${s.status}`)}</span></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>
</div>
