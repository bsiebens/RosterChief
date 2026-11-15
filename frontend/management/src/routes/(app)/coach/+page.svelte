<script lang="ts">
	import { page } from '$app/state';
	import { CalendarDays, ChevronRight, ClipboardCheck, ListChecks, Shirt, Users } from '@lucide/svelte';
	import { formatDateTime, formatLongDay, formatTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import InstallPush from '#lib/components/InstallPush.svelte';
	import PortalEventDate from '#lib/components/PortalEventDate.svelte';
	import EventHero from '#lib/components/portal/EventHero.svelte';
	import News from '#lib/components/portal/News.svelte';

	let { data } = $props();
	let selected = $state('');
	type CoachEvent = (typeof data.coach.events)[number];
	const teams = $derived(data.coach.teams);
	const activeTeam = $derived(teams.find((team) => team.id === selected) ?? teams[0]);
	const events = $derived(data.coach.events.filter((event) => !activeTeam || event.team_ids.includes(activeTeam.id)));
	const session = $derived(events.find((event) => !event.cancelled && Date.parse(event.start) >= Date.now()) ?? events.find((event) => !event.cancelled));
	const upcoming = $derived(events.filter((event) => !event.cancelled && event.can_manage_attendance && Date.parse(event.start) > Date.now()));
	const lineupDue = $derived(upcoming.find((event) => event.lineup && event.lineup_published === false));
	const repliesDue = $derived(upcoming.find((event) => event.unanswered > 0 && (!event.deadline || Date.parse(event.deadline) >= Date.now())));
	const tab = $derived(page.url.searchParams.get('tab') || page.url.hash.slice(1) || 'home');
	const labels: Record<string, string> = { home: 'portal.today', calendar: 'shell.nav.events', teams: 'portal.squad', news: 'shell.nav.news' };
	const title = $derived(t(labels[tab] ?? labels.home));
	const eventHref = (event: CoachEvent) => `/calendar/events/${event.id}?view=coach`;
	usePageHeader(() => ({ title, context: selected ? activeTeam?.name : undefined }));
</script>

<svelte:head><title>{pageTitle(title)}</title></svelte:head>

{#snippet actions(event: CoachEvent, featured = false)}
	{#if !event.cancelled && (event.can_manage_attendance || event.lineup)}
		<div class="flex flex-wrap gap-2">
			{#if event.can_manage_attendance}<a class="btn min-h-11 gap-2 {featured ? 'btn-primary' : 'btn-outline'}" href="/coach/attendance/{event.id}"><ClipboardCheck size={16} aria-hidden="true" />{t(featured ? 'portal.take_attendance' : 'events.tab.attendance')}</a>{/if}
			{#if event.lineup}<a class="btn btn-outline min-h-11 gap-2" href="/calendar/events/{event.id}?tab=lineup&view=coach"><Shirt size={16} aria-hidden="true" />{t('events.tab.lineup')}</a>{/if}
		</div>
	{/if}
{/snippet}

{#snippet squad()}
	<section class="card card-body">
		<h2 class="card-title"><Users size={16} aria-hidden="true" />{t('portal.teams')}</h2>
		<div class="divide-y divide-rule">
			{#each teams as team (team.id)}
				<a class="flex min-h-12 items-center justify-between gap-2 py-2 font-semibold hover:text-brand" href="/coach/teams/{team.id}">{team.name}<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" /></a>
			{:else}
				<p class="py-2 text-sm text-muted">{t('portal.no_teams')}</p>
			{/each}
		</div>
	</section>
{/snippet}

<div class="flex flex-wrap items-baseline justify-between gap-2 md:hidden">
	<h1 class="font-display text-2xl font-extrabold text-ink uppercase">{title}</h1>
	{#if tab === 'home'}<p class="text-sm text-muted">{formatLongDay(new Date())}</p>{/if}
</div>
{#if teams.length > 1 && (tab === 'home' || tab === 'calendar')}
	<div class="flex max-w-full gap-2 overflow-x-auto pb-1" role="group" aria-label={t('portal.teams')}>
		{#each teams as team (team.id)}
			<button class="portal-person !px-3" aria-pressed={activeTeam?.id === team.id} type="button" onclick={() => (selected = team.id)}>{team.name}</button>
		{/each}
	</div>
{/if}

{#if tab === 'news'}
	<News articles={data.coach.news ?? []} path="/coach" />
{:else if tab === 'teams'}
	{@render squad()}
{:else if tab === 'calendar'}
	<section class="card card-body">
		<h2 class="card-title"><CalendarDays size={16} aria-hidden="true" />{t('portal.schedule')}</h2>
		<div class="divide-y divide-rule">
			{#each events as event (event.id)}
				<article class="flex gap-3 py-3">
					<PortalEventDate start={event.start} />
					<div class="grid min-w-0 flex-1 gap-2">
						<a class="flex items-center justify-between gap-2 font-semibold text-ink hover:text-brand" href={eventHref(event)}>{event.title}<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" /></a>
						<p class="text-sm text-muted">{formatTime(event.start)}{event.location ? ' · ' + event.location : ''}</p>
						{#if event.cancelled}<span class="badge badge-error w-fit">{t('portal.cancelled')}</span>{:else}{@render actions(event)}{/if}
					</div>
				</article>
			{:else}
				<p class="py-2 text-sm text-muted">{t('portal.no_events')}</p>
			{/each}
		</div>
	</section>
{:else}
	<div class="grid min-w-0 items-start gap-4 md:grid-cols-[minmax(0,5fr)_minmax(0,4fr)]">
		{#if session}
			<article class="card overflow-hidden">
				<h2 class="card-title px-4 py-3"><CalendarDays size={16} aria-hidden="true" />{t(new Date(session.start).toDateString() === new Date().toDateString() ? 'portal.today_session' : 'portal.next_session')}</h2>
				<a class="block" href={eventHref(session)}><EventHero event={session} background={data.coach.event_background} crest={data.branding?.logo_url} /></a>
				<div class="grid gap-3 p-4">
					<div class="grid grid-cols-3 divide-x divide-line">
					{#each [[session.going, 'portal.going'], [session.out, 'portal.out'], [session.unanswered, 'portal.no_reply']] as [count, label] (label)}
						<div class="px-1 text-center">
							<div class="font-display text-2xl leading-none font-extrabold text-ink">{count ?? 0}</div>
							<div class="mt-1 text-xs text-muted">{t(String(label))}</div>
						</div>
					{/each}
					</div>
					{#if session.deadline}<p class="text-xs text-muted">{t('events.deadline')} · {formatDateTime(session.deadline)}</p>{/if}
					{@render actions(session, true)}
				</div>
			</article>
		{:else}
			<p class="card card-body text-sm text-muted">{t('portal.no_events')}</p>
		{/if}
		<div class="grid min-w-0 gap-4">
			{#if lineupDue || repliesDue}
				<section class="card card-body">
					<h2 class="card-title"><ListChecks size={16} aria-hidden="true" />{t('portal.needs_you')}</h2>
					<div class="divide-y divide-rule">
						{#if lineupDue}
							<a class="flex min-h-11 items-center justify-between gap-2 py-2 hover:text-brand" href="/calendar/events/{lineupDue.id}?tab=lineup&view=coach">
								<span><strong class="block">{t('portal.lineup_due')}</strong><span class="block text-xs text-muted">{lineupDue.title} · {formatDateTime(lineupDue.start)}</span></span>
								<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
							</a>
						{/if}
						{#if repliesDue}
							<a class="flex min-h-11 items-center justify-between gap-2 py-2 hover:text-brand" href="/calendar/events/{repliesDue.id}?tab=attendance&view=coach">
								<span><strong class="block">{t('portal.replies_due', { count: repliesDue.unanswered })}</strong><span class="block text-xs text-muted">{repliesDue.title} · {formatDateTime(repliesDue.start)}</span></span>
								<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
							</a>
						{/if}
					</div>
				</section>
			{/if}
			{@render squad()}
			<InstallPush />
		</div>
	</div>
{/if}
