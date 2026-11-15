<script lang="ts">
	import { ArrowLeft, ChevronRight, Newspaper, Search } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { formatDay } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { mediaPath } from '#lib/media.ts';
	import type { PortalArticle } from '#lib/portal.ts';
	import Sheet from './Sheet.svelte';

	let { articles, home = false, path = '/parent' }: { articles: PortalArticle[]; home?: boolean; path?: string } = $props();
	// From tablet width an article opens in the reading drawer; on a phone it takes the whole page (the back button returns to the list).
	let desktop = $state(false);
	onMount(() => {
		const query = matchMedia('(min-width: 48rem)');
		desktop = query.matches;
		const update = () => (desktop = query.matches);
		query.addEventListener('change', update);
		return () => query.removeEventListener('change', update);
	});
	const closeHref = $derived(home ? path : path + '?tab=news');
	let team = $state('');
	let query = $state('');
	const teamNames = $derived([...new Set(articles.flatMap((article) => article.teams ?? []))].sort());
	const hasClubNews = $derived(articles.some((article) => !(article.teams ?? []).length));
	const matches = (article: PortalArticle) => {
		if (team === '__club') return !(article.teams ?? []).length;
		if (team && !(article.teams ?? []).includes(team)) return false;
		const needle = query.trim().toLowerCase();
		return !needle || `${translation(article).title} ${translation(article).excerpt}`.toLowerCase().includes(needle);
	};
	const shown = $derived(home ? articles.slice(0, 4) : articles.filter(matches));
	const featured = $derived(home ? undefined : shown[0]);
	const rest = $derived(home ? shown : shown.slice(1));
	const selected = $derived(articles.find((article) => article.id === page.url.searchParams.get('article')));
	const translation = (article: PortalArticle) => article.translations[getLang() === 'nl' ? 'nl' : 'en'];
	const photo = (article: PortalArticle) => article.photos.find((image) => image.is_main) ?? article.photos[0];
</script>

{#snippet articleView()}
	{#if selected}
		{@const lead = photo(selected)}
		{#if lead}<img class="aspect-video max-h-96 w-full object-cover" src={mediaPath(lead.url)} style:object-position={lead.object_position} alt="" />{/if}
		<article class="mx-auto grid max-w-2xl gap-4 px-6 py-6">
			<p class="font-mono text-[11px] tracking-[.14em] text-muted uppercase">{(selected.teams ?? []).join(', ') || t('portal.news')}{#if selected.published_at} · {formatDay(selected.published_at)}{/if}</p>
			<h2 class="font-display text-4xl leading-none font-extrabold text-ink uppercase">{translation(selected).title}</h2>
			<div class="prose max-w-none text-base leading-relaxed">{@html translation(selected).body}</div>
			{#each selected.photos.filter((image) => image !== lead) as image (image.url)}
				<img class="w-full rounded-box object-cover" src={mediaPath(image.url)} style:object-position={image.object_position} alt="" loading="lazy" />
			{/each}
		</article>
	{/if}
{/snippet}

{#if selected && !desktop}
	<a class="btn btn-outline mb-3 justify-self-start gap-2" href={closeHref} data-sveltekit-reset="false"><ArrowLeft size={16} aria-hidden="true" />{t('portal.news')}</a>
	<div class="card overflow-hidden">{@render articleView()}</div>
{:else if home}
	<div class="mb-2 flex items-center justify-between gap-2">
		<h2 class="card-title"><Newspaper size={16} aria-hidden="true" />{t('portal.news')}</h2>
		<a class="link-more" href={path + '?tab=news'}>{t('overview.news.view_all')} →</a>
	</div>
{/if}
{#if !home}
	<div class="mb-4 flex flex-wrap items-center gap-2">
		<div class="flex flex-wrap gap-1.5" role="group" aria-label={t('portal.news_filter')}>
			<button type="button" class="portal-person compact text" aria-pressed={team === ''} onclick={() => (team = '')}>{t('portal.all')}</button>
			{#each teamNames as name (name)}<button type="button" class="portal-person compact text" aria-pressed={team === name} onclick={() => (team = name)}>{name}</button>{/each}
			{#if hasClubNews}<button type="button" class="portal-person compact text" aria-pressed={team === '__club'} onclick={() => (team = '__club')}>{t('portal.news_club')}</button>{/if}
		</div>
		<label class="relative ml-auto w-full sm:w-64"><span class="sr-only">{t('portal.news_search')}</span><Search size={14} class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-dim" aria-hidden="true" /><input class="input w-full pl-9" type="search" placeholder={t('portal.news_search')} bind:value={query} /></label>
	</div>
{/if}
{#if shown.length}
	<div class={home ? '' : 'grid items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]'}>
		{#if featured}
			{@const lead = photo(featured)}
			<a class="card group block overflow-hidden hover:border-club" href={path + '?tab=news&article=' + featured.id} data-sveltekit-reset="false">
				{#if lead}<img class="aspect-video w-full object-cover xl:max-h-80" src={mediaPath(lead.url)} style:object-position={lead.object_position} alt="" loading="lazy" />{:else}<div class="grid aspect-video place-items-center bg-subhead text-dim xl:max-h-80"><Newspaper size={36} aria-hidden="true" /></div>{/if}
				<div class="grid gap-2 p-5">
					<p class="font-mono text-[11px] tracking-[.14em] text-muted uppercase">{(featured.teams ?? []).join(', ') || t('portal.news')}{#if featured.published_at} · {formatDay(featured.published_at)}{/if}</p>
					<h2 class="font-display text-3xl leading-none font-extrabold text-ink uppercase">{translation(featured).title}</h2>
					{#if translation(featured).excerpt}<p class="line-clamp-3 text-slate">{translation(featured).excerpt}</p>{/if}
					<span class="link-more">{t('portal.read_more')} →</span>
				</div>
			</a>
		{/if}
		<div class={home ? 'grid gap-3 md:grid-cols-2' : 'grid gap-3'}>
			{#each rest as article (article.id)}
				{@const lead = photo(article)}
				<a class="card group flex gap-3 p-3 hover:border-club" href={path + (home ? '?' : '?tab=news&') + 'article=' + article.id} data-sveltekit-reset="false">
					{#if lead}
						<img class="h-[4.5rem] w-24 shrink-0 rounded object-cover" src={mediaPath(lead.url)} style:object-position={lead.object_position} alt="" loading="lazy" />
					{:else}
						<div class="grid h-[4.5rem] w-24 shrink-0 place-items-center rounded bg-subhead text-dim"><Newspaper size={22} aria-hidden="true" /></div>
					{/if}
					<div class="flex min-w-0 flex-1 items-center justify-between gap-2">
						<div class="min-w-0">
							<p class="truncate font-display text-xs font-bold tracking-wide text-muted uppercase">{(article.teams ?? []).join(', ') || t('portal.news')}{#if article.published_at} · {formatDay(article.published_at)}{/if}</p>
							<h3 class="line-clamp-2 font-display text-base leading-tight font-extrabold text-ink uppercase">{translation(article).title}</h3>
						</div>
						<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
					</div>
				</a>
			{/each}
		</div>
	</div>
{:else}
	<p class="card card-body text-sm text-muted">{t('portal.no_news')}</p>
{/if}
{#if selected && desktop}
<Sheet wide open={true} title={translation(selected).title} onclose={() => void goto(closeHref, { replaceState: true })}>
	{@render articleView()}
</Sheet>
{/if}
