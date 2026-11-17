<script lang="ts">
	import { page } from '$app/state';
	import { Plus } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import HeaderChip from '#lib/components/mobile/HeaderChip.svelte';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import CoachEvents from '#lib/components/coach/CoachEvents.svelte';
	import CoachNews from '#lib/components/coach/CoachNews.svelte';
	import CoachSquad from '#lib/components/coach/CoachSquad.svelte';
	import CoachToday from '#lib/components/coach/CoachToday.svelte';

	let { data } = $props();
	// `?team=` may list several teams (the teams of the event the coach is coming back from): the first one this coach is staff of is shown.
	const preferred = (page.url.searchParams.get('team') ?? '').split(',').filter(Boolean);
	let selected = $state('');
	const teams = $derived(data.coach.teams);
	const activeTeam = $derived(teams.find((team) => team.id === selected) ?? teams.find((team) => preferred.includes(team.id)) ?? teams[0]);
	const events = $derived(data.coach.events.filter((event) => !activeTeam || event.team_ids.includes(activeTeam.id)));
	const roster = $derived(data.rosters.find((team) => team.id === activeTeam?.id) ?? null);
	// Coaches see their own drafts and published items; the list the API gives (null when it failed) falls back to the published items of the home payload.
	const newsItems = $derived(
		data.newsItems ??
			data.coach.news.map((article) => ({ id: article.id, title: article.translations.en.title || article.translations.nl.title, slug: '', status: 'published' as const, visibility: 'internal' as const, published_at: article.published_at, is_scheduled: false, created: article.published_at, created_by: null, team_ids: [], teams: article.teams.map((name) => ({ id: name, name })), main_photo_url: article.photos.find((photo) => photo.is_main)?.url ?? article.photos[0]?.url ?? null, can_edit: false }))
	);
	const tab = $derived(page.url.searchParams.get('tab') || page.url.hash.slice(1) || 'home');
	const labels: Record<string, string> = { home: 'portal.today', calendar: 'shell.nav.events', teams: 'portal.squad', news: 'shell.nav.news' };
	const title = $derived(t(labels[tab] ?? labels.home));
	// The phone header is the board's: the date above "Today", the squad size above "Squad", the team chip and the bell; larger screens keep the team name as context.
	let phone = $state(false);
	$effect(() => {
		const query = window.matchMedia('(max-width: 47.99rem)');
		phone = query.matches;
		const update = () => (phone = query.matches);
		query.addEventListener('change', update);
		return () => query.removeEventListener('change', update);
	});
	const shortDay = (date: Date) => new Intl.DateTimeFormat(getLang(), { weekday: 'short', day: 'numeric', month: 'short' }).format(date);
	const headerContext = $derived(
		phone
			? tab === 'home'
				? shortDay(new Date())
				: tab === 'teams' && roster
					? t('coach.squad_counts', { players: roster.roster.length, staff: roster.staff.length })
					: undefined
			: teams.length > 1 || tab !== 'home'
				? activeTeam?.name
				: undefined
	);
	const teamOptions = $derived(teams.map((team) => ({ id: team.id, name: team.name, short: team.name.replace(/^.*\s(?=\S+$)/, '') || team.name })));
	usePageHeader(() => ({ title, context: headerContext, actions: pageActions }));
</script>

<svelte:head><title>{pageTitle(title)}</title></svelte:head>

{#snippet pageActions()}
	{#if teams.length > 1 && tab !== 'news'}<div class="md:hidden"><HeaderChip options={teamOptions} bind:value={() => activeTeam?.id ?? '', (id) => (selected = id)} /></div>{/if}
{/snippet}

{#snippet addButton(href: string, label: string)}
	<!-- On a phone the button stays fixed above the tab bar while the list scrolls behind it. -->
	<a class="btn btn-primary min-h-12 gap-2 max-md:fixed max-md:inset-x-3 max-md:bottom-[calc(3.5rem+env(safe-area-inset-bottom)+0.5rem)] max-md:z-20 max-md:mx-auto max-md:max-w-2xl max-md:shadow-lg md:mb-3 md:min-h-11 md:w-auto" {href}><Plus size={18} aria-hidden="true" />{label}</a>
{/snippet}


{#if teams.length > 1 && tab !== 'news'}
	<div class="flex max-w-full gap-2 overflow-x-auto pb-1 max-md:hidden" role="group" aria-label={t('portal.teams')}>
		{#each teams as team (team.id)}
			<button class="portal-person compact text" aria-pressed={activeTeam?.id === team.id} type="button" onclick={() => (selected = team.id)}>{team.name}</button>
		{/each}
	</div>
{/if}

{#if tab === 'news'}
	{#if data.coach.can_add_news}{@render addButton('/coach/news/new', t('coach.add_post'))}{/if}
	<CoachNews items={newsItems} articles={data.coach.news} mine={page.url.searchParams.get('mine') === '1'} canFilter={data.coach.can_add_news} />
	{#if data.coach.can_add_news}<div class="h-16 md:hidden" aria-hidden="true"></div>{/if}
{:else if tab === 'teams'}
	<CoachSquad team={roster} positions={data.positions} />
{:else if tab === 'calendar'}
	{#if data.coach.can_create_events}{@render addButton('/calendar/events/new?view=coach', t('coach.add_event'))}{/if}
	<CoachEvents {events} />
	{#if data.coach.can_create_events}<div class="h-16 md:hidden" aria-hidden="true"></div>{/if}
{:else}
	<CoachToday {events} background={data.coach.event_background} crest={data.branding?.logo_url} />
{/if}
