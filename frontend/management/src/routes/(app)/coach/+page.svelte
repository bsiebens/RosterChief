<script lang="ts">
	import { page } from '$app/state';
	import { Plus } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import InstallPush from '#lib/components/InstallPush.svelte';
	import CoachEvents from '#lib/components/coach/CoachEvents.svelte';
	import CoachNews from '#lib/components/coach/CoachNews.svelte';
	import CoachSquad from '#lib/components/coach/CoachSquad.svelte';
	import CoachToday from '#lib/components/coach/CoachToday.svelte';

	let { data } = $props();
	let selected = $state(page.url.searchParams.get('team') ?? '');
	const teams = $derived(data.coach.teams);
	const activeTeam = $derived(teams.find((team) => team.id === selected) ?? teams[0]);
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
	usePageHeader(() => ({ title, context: teams.length > 1 || tab !== 'home' ? activeTeam?.name : undefined, actions: tab === 'calendar' && data.coach.can_create_events ? addEvent : tab === 'news' && data.coach.can_add_news ? addPost : null }));
</script>

<svelte:head><title>{pageTitle(title)}</title></svelte:head>

{#snippet addEvent()}<a class="btn btn-primary btn-square" href="/calendar/events/new?view=coach" aria-label={t('coach.add_event')}><Plus size={18} aria-hidden="true" /></a>{/snippet}
{#snippet addPost()}<a class="btn btn-primary btn-square" href="/news/new" aria-label={t('coach.add_post')}><Plus size={18} aria-hidden="true" /></a>{/snippet}

{#if teams.length > 1 && tab !== 'news'}
	<div class="flex max-w-full gap-2 overflow-x-auto pb-1" role="group" aria-label={t('portal.teams')}>
		{#each teams as team (team.id)}
			<button class="portal-person compact text" aria-pressed={activeTeam?.id === team.id} type="button" onclick={() => (selected = team.id)}>{team.name}</button>
		{/each}
	</div>
{/if}

{#if tab === 'news'}
	<CoachNews items={newsItems} articles={data.coach.news} />
{:else if tab === 'teams'}
	<CoachSquad team={roster} />
{:else if tab === 'calendar'}
	<CoachEvents {events} />
{:else}
	<CoachToday {events} background={data.coach.event_background} crest={data.branding?.logo_url} />
	<InstallPush />
{/if}
