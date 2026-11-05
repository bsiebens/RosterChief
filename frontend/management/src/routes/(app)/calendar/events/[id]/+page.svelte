<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { ArrowLeft, Ban, CalendarClock, Clock, ClipboardList, Flag, MapPin, Paperclip, Pencil, Repeat, RotateCcw, Shirt, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Tabs from '#lib/components/Tabs.svelte';
	import EventAttachments from '#lib/components/events/EventAttachments.svelte';
	import EventAttendance from '#lib/components/events/EventAttendance.svelte';
	import EventLineup from '#lib/components/events/EventLineup.svelte';
	import EventTasks from '#lib/components/events/EventTasks.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { describeRecurrence, eventEditHref, formatDateTime, formatDay, formatRange, isoDate, kindLabelKey, kindStyle, statusLabelKey, statusStyle } from '#lib/events/types.ts';

	let { data, form } = $props();
	const ev = $derived(data.event);
	const style = $derived(kindStyle(ev.kind));
	const past = $derived(new Date(ev.start).getTime() < Date.now());
	const series = $derived(ev.series ? describeRecurrence(ev.series.recurrence, ev.series.description) : '');
	const mapsHref = $derived(ev.location ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([ev.location.name, ev.location.city].filter(Boolean).join(', '))}` : '');
	const calendarHref = $derived(`/calendar?date=${isoDate(new Date(ev.start))}`);

	const tabs = $derived([
		{ key: 'overview', label: t('events.tab.overview') },
		{ key: 'attendance', label: `${t('events.tab.attendance')} · ${ev.attendance.total}` },
		...(ev.lineup.available ? [{ key: 'lineup', label: t('events.tab.lineup') }] : []),
		{ key: 'tasks', label: `${t('events.tab.tasks')} · ${data.tasks?.tasks.length ?? ev.task_count}` },
		{ key: 'files', label: `${t('events.tab.files')} · ${data.attachments?.attachments.length ?? ev.attachment_count}` }
	]);

	let cancelOpen = $state(false);
	let busy = $state(false);
	const submit = () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') cancelOpen = false;
		};
	};

	usePageHeader(() => ({ title: ev.title, context: `${t(kindLabelKey(ev.kind))} · ${formatDay(ev.start)}`, actions }));
</script>

{#snippet actions()}
	{#if ev.can_edit}
		<a class="btn btn-outline gap-2" href={eventEditHref(ev.id)}><Pencil size={16} /> {t('common.edit')}</a>
		<button class="btn btn-outline gap-2 {ev.cancelled ? '' : 'btn-error'}" type="button" onclick={() => (cancelOpen = true)}>
			{#if ev.cancelled}<RotateCcw size={16} /> {t('events.reactivate')}{:else}<Ban size={16} /> {t('events.cancel')}{/if}
		</button>
	{/if}
	<a class="btn btn-outline gap-2" href={calendarHref}><ArrowLeft size={16} /> {t('events.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(ev.title)}</title></svelte:head>

<div class="grid gap-5">
	{#if ev.cancelled}
		<Alert kind="warning" title={t('events.cancelled_title')}>{t('events.cancelled_body')}</Alert>
	{/if}
	{#if form?.cancelError}<Alert kind="error">{form.cancelError}</Alert>{/if}

	<section class="card border-l-4 {style.bar}">
		<div class="grid gap-4 p-6 lg:grid-cols-[1fr_auto]">
			<div class="min-w-0">
				<div class="flex flex-wrap items-center gap-1.5">
					<span class="badge badge-sm border {style.chip}">{t(kindLabelKey(ev.kind))}</span>
					{#if ev.cancelled}<span class="badge badge-sm badge-error">{t('events.cancelled')}</span>{/if}
					{#if ev.is_live}<span class="badge badge-sm badge-error">{t('events.live')}</span>{/if}
					{#if ev.is_friendly}<span class="badge badge-sm badge-outline">{t('events.friendly')}</span>{/if}
					{#if ev.competition_label}<span class="badge badge-sm badge-outline">{ev.competition_label}</span>{/if}
					{#if ev.opponent}<span class="badge badge-sm badge-ghost">{ev.is_home_game ? t('events.home') : t('events.away')}</span>{/if}
				</div>
				<h1 class="mt-2 font-display text-4xl leading-none font-extrabold text-ink uppercase {ev.cancelled ? 'line-through decoration-4' : ''}">{ev.title}</h1>
				<p class="mt-2 flex items-center gap-2 font-semibold text-slate"><CalendarClock size={16} aria-hidden="true" /> {formatRange(ev.start, ev.end)}</p>
				<dl class="mt-3 grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
					{#if ev.gathering}
						<div class="flex items-center gap-2"><dt class="flex items-center gap-1.5 text-muted"><Clock size={14} aria-hidden="true" /> {t('events.gathering')}</dt><dd class="font-semibold">{formatDateTime(ev.gathering)}</dd></div>
					{/if}
					{#if ev.deadline}
						<div class="flex items-center gap-2"><dt class="flex items-center gap-1.5 text-muted"><Flag size={14} aria-hidden="true" /> {t('events.deadline')}</dt><dd class="font-semibold">{formatDateTime(ev.deadline)}</dd></div>
					{/if}
					{#if ev.location}
						<div class="flex items-center gap-2">
							<dt class="flex items-center gap-1.5 text-muted"><MapPin size={14} aria-hidden="true" /> {t('events.location')}</dt>
							<dd><a class="link font-semibold" href={mapsHref} target="_blank" rel="noreferrer" title={t('events.open_map')}>{ev.location.name}{ev.location.city ? `, ${ev.location.city}` : ''}</a></dd>
						</div>
					{/if}
					<div class="flex items-start gap-2">
						<dt class="flex items-center gap-1.5 text-muted"><Users size={14} aria-hidden="true" /> {t('events.audience')}</dt>
						<dd class="flex flex-wrap gap-x-2 gap-y-0.5">
							{#if ev.club_wide}<span class="font-semibold">{t('events.whole_club')}</span>{/if}
							{#each ev.teams as team (team.id)}<a class="link font-semibold" href="/teams/{team.id}">{team.name}</a>{/each}
							{#each ev.groups as g (g.id)}<span class="font-semibold">{g.name}</span>{/each}
							{#if !ev.club_wide && !ev.teams.length && !ev.groups.length}<span class="text-muted">{ev.invited_members.length ? t('events.only_invited') : '—'}</span>{/if}
						</dd>
					</div>
				</dl>
				{#if ev.series}
					<p class="mt-3 flex items-center gap-2 text-sm text-slate">
						<Repeat size={14} aria-hidden="true" />
						<span>{series}{#if ev.detached} · <span class="text-muted">{t('events.detached')}</span>{/if}</span>
					</p>
				{/if}
			</div>
			{#if ev.opponent}
				<div class="flex items-center gap-4 self-center">
					<div class="text-right">
						<span class="block font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('events.opponent')}</span>
						<span class="font-display text-xl font-extrabold text-ink uppercase">{ev.opponent.name}</span>
						{#if ev.score_for !== null && ev.score_against !== null}
							<span class="block font-display text-3xl font-extrabold text-ink">{ev.score_for} – {ev.score_against}</span>
						{/if}
					</div>
					<ClubCrest name={ev.opponent.name} logoUrl={ev.opponent.logo_url} size={64} />
				</div>
			{/if}
		</div>
	</section>

	<Tabs tabs={tabs} active={data.tab} label={t('events.tabs')} />

	{#if data.tab === 'overview'}
		<div class="grid gap-4 lg:grid-cols-3">
			<a class="card card-body gap-2 hover:shadow-md" href="?tab=attendance">
				<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><Users size={16} aria-hidden="true" /> {t('events.tab.attendance')}</h2>
				{#if ev.attendance.total}
					<ul class="flex flex-wrap gap-1.5">
						{#each ['present', 'selected', 'absent', 'excused', 'maybe', 'not_selected', 'no_response'] as s (s)}
							{#if ev.attendance[s as keyof typeof ev.attendance]}<li class="badge badge-sm border {statusStyle(s)}">{t(statusLabelKey(s))} · {ev.attendance[s as keyof typeof ev.attendance]}</li>{/if}
						{/each}
					</ul>
					<p class="text-sm text-muted">{t('events.att.invited_n', { n: ev.attendance.total })}</p>
				{:else}
					<p class="text-sm text-muted">{t('events.att.none')}</p>
				{/if}
			</a>
			{#if ev.lineup.available}
				<a class="card card-body gap-2 hover:shadow-md" href="?tab=lineup">
					<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><Shirt size={16} aria-hidden="true" /> {t('events.tab.lineup')}</h2>
					<p class="text-sm">{t(`events.lineup.status.${ev.lineup.status}`)}</p>
					{#if ev.lineup.published_at}<p class="font-mono text-xs text-muted">{t('events.lineup.published_at', { when: formatDateTime(ev.lineup.published_at) })}</p>{:else if ev.lineup.scheduled_publish_at}<p class="font-mono text-xs text-muted">{t('events.lineup.scheduled_at', { when: formatDateTime(ev.lineup.scheduled_publish_at) })}</p>{/if}
				</a>
			{/if}
			<a class="card card-body gap-2 hover:shadow-md" href="?tab=tasks">
				<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><ClipboardList size={16} aria-hidden="true" /> {t('events.tab.tasks')}</h2>
				{#if data.tasks?.tasks.length}
					<p class="text-sm">{t('events.tasks.summary', { n: data.tasks.tasks.length, open: data.tasks.tasks.reduce((n, x) => n + x.open_slots, 0) })}</p>
				{:else}
					<p class="text-sm text-muted">{t('events.tasks.none')}</p>
				{/if}
			</a>
			<a class="card card-body gap-2 hover:shadow-md" href="?tab=files">
				<h2 class="flex items-center gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase"><Paperclip size={16} aria-hidden="true" /> {t('events.tab.files')}</h2>
				<p class="text-sm {ev.attachment_count ? '' : 'text-muted'}">{ev.attachment_count ? t('events.files.count', { n: ev.attachment_count }) : t('events.files.none')}</p>
			</a>
			<div class="card card-body gap-2 lg:col-span-2">
				<h2 class="font-display text-base font-extrabold tracking-[.04em] text-ink uppercase">{t('events.details')}</h2>
				<dl class="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[auto_1fr]">
					{#if ev.season}<dt class="text-muted">{t('events.season')}</dt><dd>{ev.season.name}</dd>{/if}
					{#if ev.invited_members.length}<dt class="text-muted">{t('events.also_invited')}</dt><dd class="flex flex-wrap gap-x-2">{#each ev.invited_members as m (m.id)}<a class="link" href="/members/{m.id}">{m.name}</a>{/each}</dd>{/if}
					{#if ev.excluded_members.length}<dt class="text-muted">{t('events.excluded')}</dt><dd class="flex flex-wrap gap-x-2">{#each ev.excluded_members as m (m.id)}<a class="link" href="/members/{m.id}">{m.name}</a>{/each}</dd>{/if}
					{#if ev.created_by}<dt class="text-muted">{t('events.created_by')}</dt><dd>{ev.created_by.name}</dd>{/if}
					{#if ev.series}<dt class="text-muted">{t('events.series')}</dt><dd>{series}</dd>{/if}
				</dl>
			</div>
		</div>
	{:else if data.tab === 'attendance'}
		{#if data.attendance}<EventAttendance eventId={ev.id} initial={data.attendance} {past} cancelled={ev.cancelled} />{:else}<Alert kind="error">{t('common.error.generic')}</Alert>{/if}
	{:else if data.tab === 'lineup'}
		{#if data.lineup}<EventLineup eventId={ev.id} initial={data.lineup} cancelled={ev.cancelled} />{:else}<Alert kind="error">{t('common.error.generic')}</Alert>{/if}
	{:else if data.tab === 'tasks'}
		{#if data.tasks}<EventTasks eventId={ev.id} initial={data.tasks} />{:else}<Alert kind="error">{t('common.error.generic')}</Alert>{/if}
	{:else if data.tab === 'files'}
		{#if data.attachments}<EventAttachments eventId={ev.id} initial={data.attachments} />{:else}<Alert kind="error">{t('common.error.generic')}</Alert>{/if}
	{/if}
</div>

<Modal bind:open={cancelOpen} title={ev.cancelled ? t('events.reactivate_title') : t('events.cancel_title')}>
	<form method="POST" action={ev.cancelled ? '?/uncancel' : '?/cancel'} class="grid gap-3" use:enhance={submit}>
		<p>{ev.cancelled ? t('events.reactivate_body', { name: ev.title }) : t('events.cancel_body', { name: ev.title })}</p>
		{#if ev.series_id && !ev.cancelled}<p class="text-sm text-muted">{t('events.cancel_series_note')}</p>{/if}
		<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" class="checkbox" name="notify" checked /> {t('events.notify_invited')}</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (cancelOpen = false)}>{t('common.back')}</button>
			{#if ev.cancelled}
				<button class="btn btn-primary gap-2" type="submit" disabled={busy}><RotateCcw size={16} /> {t('events.reactivate')}</button>
			{:else}
				<button class="btn btn-error gap-2" type="submit" disabled={busy}><Ban size={16} /> {t('events.cancel')}</button>
			{/if}
		</div>
	</form>
</Modal>
