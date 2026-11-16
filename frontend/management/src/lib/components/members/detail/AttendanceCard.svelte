<script lang="ts">
	import { ChartLine, History, CalendarCheck, CircleCheck, CircleX, Minus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { eventHref, formatEventDate, formatRate, kindLabel, rateTone, SPARK_STYLE, sparkLabel, statusLabel, statusTone, TONE, type MemberAttendance } from '#lib/attendance.ts';

	/** The member's attendance this season (GET /members/{id}/attendance): turnout, totals, the last 12 events and the most recent past ones. Null when it could not be loaded. */
	let { attendance }: { attendance: MemberAttendance | null } = $props();

	const counts = $derived(attendance?.counts ?? { present: 0, absent: 0, no_reply: 0, no_shows: 0 });
	const answered = $derived(counts.present + counts.absent + counts.no_reply);
	const stats = $derived([
		{ key: 'present', n: counts.present, tone: TONE.ok.bar },
		{ key: 'absent', n: counts.absent, tone: TONE.danger.bar },
		{ key: 'no_reply', n: counts.no_reply, tone: 'bg-dim' }
	]);
	const tone = $derived(TONE[rateTone(attendance?.rate)]);
</script>

{#if !attendance}
	<Alert kind="error">{t('attbox.failed')}</Alert>
{:else}
	<div class="grid gap-4 lg:grid-cols-[22rem_1fr]">
		<div class="grid content-start gap-4">
			<div class="card card-body">
				<h2 class="card-title"><CalendarCheck size={18} /> {t('profile.att.rate')}</h2>
				<p class="font-display text-5xl font-extrabold {attendance.rate === null ? 'text-muted' : tone.text}">{formatRate(attendance.rate)}</p>
				<p class="text-sm text-muted">
					{#if attendance.season}{t('attbox.season', { name: attendance.season.name })} · {/if}{answered ? t('attbox.answered', { n: answered }) : t('attbox.no_data')}
				</p>
				{#if answered}
					<div class="mt-2 flex h-2.5 overflow-hidden rounded-full bg-rule">
						{#each stats as s (s.key)}<div class="{s.tone} h-full" style="width: {(s.n / answered) * 100}%"></div>{/each}
					</div>
				{/if}
				<ul class="mt-2 grid gap-1.5 text-sm">
					{#each stats as s (s.key)}
						<li class="flex items-center justify-between"><span class="inline-flex items-center gap-2 text-muted"><span class="h-2.5 w-2.5 rounded-full {s.tone}"></span>{t(`attbox.${s.key}`)}</span><span class="font-mono text-xs font-semibold text-ink">{s.n}</span></li>
					{/each}
					<li class="flex items-center justify-between border-t border-rule pt-1.5" title={t('attbox.no_shows_help')}>
						<span class="inline-flex items-center gap-2 text-muted"><CircleX size={12} class="text-danger" aria-hidden="true" />{t('attbox.no_shows')}</span>
						<span class="font-mono text-xs font-semibold {counts.no_shows ? 'text-danger' : 'text-ink'}">{counts.no_shows}</span>
					</li>
				</ul>
			</div>
			<div class="card card-body">
				<h2 class="card-title text-base flex items-center gap-2"><ChartLine size={16} class="shrink-0" aria-hidden="true" /> {t('attbox.sparkline', { n: 12 })}</h2>
				{#if attendance.sparkline.length}
					<div class="grid grid-cols-6 gap-2">
						{#each attendance.sparkline as bar (bar.id)}
							{@const label = `${bar.title} · ${formatEventDate(bar.start)} · ${sparkLabel(bar.state)}`}
							<a class="h-8 rounded-md {SPARK_STYLE[bar.state]} opacity-90 transition-opacity hover:opacity-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink" href={eventHref(bar.id)} title={label} aria-label={label}></a>
						{/each}
					</div>
					<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
						{#each ['present', 'absent', 'upcoming'] as const as state (state)}<span class="inline-flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-sm {SPARK_STYLE[state]}"></span>{sparkLabel(state)}</span>{/each}
					</div>
				{:else}
					<p class="py-3 text-center text-sm text-muted">{t('attbox.sparkline_none')}</p>
				{/if}
			</div>
		</div>

		<div class="card card-body">
			<h2 class="card-title text-base flex items-center gap-2"><History size={16} class="shrink-0" aria-hidden="true" /> {t('profile.att.recent')}</h2>
			<div class="overflow-x-auto">
				<table class="table">
					<thead><tr><th>{t('attbox.col.event')}</th><th>{t('attbox.col.date')}</th><th>{t('attbox.col.kind')}</th><th>{t('attbox.col.status')}</th><th class="text-center">{t('attbox.col.showed_up')}</th></tr></thead>
					<tbody>
						{#each attendance.recent as row (row.event.id)}
							<tr class="hover:bg-surface {row.cancelled ? 'text-muted' : ''}">
								<td>
									<a class="link font-semibold {row.cancelled ? 'line-through' : ''}" href={eventHref(row.event.id)}>{row.event.title}</a>
									{#if row.cancelled}<span class="badge badge-ghost badge-sm ml-1">{t('attbox.cancelled')}</span>{/if}
								</td>
								<td class="font-mono text-xs whitespace-nowrap">{formatEventDate(row.event.start)}</td>
								<td class="text-sm">{kindLabel(row.event.kind)}</td>
								<td><span class="inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold whitespace-nowrap {TONE[statusTone(row.status)].pill}">{statusLabel(row.status)}</span></td>
								<td class="text-center">
									{#if row.showed_up === true}
										<span class="inline-flex text-ok" title={t('attbox.showed_up.yes')}><CircleCheck size={16} aria-label={t('attbox.showed_up.yes')} /></span>
									{:else if row.showed_up === false}
										<span class="inline-flex text-danger" title={t('attbox.showed_up.no')}><CircleX size={16} aria-label={t('attbox.showed_up.no')} /></span>
									{:else}
										<span class="inline-flex text-dim" title={t('attbox.showed_up.unknown')}><Minus size={16} aria-label={t('attbox.showed_up.unknown')} /></span>
									{/if}
								</td>
							</tr>
						{:else}
							<tr><td colspan="5" class="py-6 text-center text-muted">{t('attbox.recent_none')}</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	</div>
{/if}
