<script lang="ts">
	import { ChevronRight, Newspaper } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { formatDay } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { PortalArticle } from '#lib/portal.ts';
	import Sheet from './Sheet.svelte';

	let { articles, home = false, path = '/parent' }: { articles: PortalArticle[]; home?: boolean; path?: string } = $props();
	const shown = $derived(home ? articles.slice(0, 3) : articles);
	const selected = $derived(articles.find((article) => article.id === page.url.searchParams.get('article')));
	const translation = (article: PortalArticle) => article.translations[getLang() === 'nl' ? 'nl' : 'en'];
	const photo = (article: PortalArticle) => article.photos.find((image) => image.is_main) ?? article.photos[0];
</script>

{#if home}
	<div class="mb-2 flex items-center justify-between gap-2">
		<h2 class="card-title"><Newspaper size={16} aria-hidden="true" />{t('portal.news')}</h2>
		<a class="link text-sm font-semibold" href={path + '?tab=news'}>{t('overview.news.view_all')}</a>
	</div>
{/if}
{#if shown.length}
	<div class={home ? 'card overflow-hidden' : 'grid gap-4 sm:grid-cols-2 2xl:grid-cols-3'}>
		{#each shown as article, i (article.id)}
			{@const lead = photo(article)}
			<a class={home ? 'group block border-b border-rule last:border-b-0 hover:bg-subhead' : 'card group block overflow-hidden hover:border-club'} href={path + (home ? '?' : '?tab=news&') + 'article=' + article.id} data-sveltekit-reset="false">
				{#if !home || i === 0}
					{#if lead}
						<img class="aspect-3/1 w-full object-cover" src={lead.url} style:object-position={lead.object_position} alt="" loading="lazy" />
					{:else}
						<div class="grid aspect-3/1 place-items-center bg-subhead text-dim"><Newspaper size={28} aria-hidden="true" /></div>
					{/if}
				{/if}
				<div class="flex items-center justify-between gap-2 px-3.5 py-3">
					<div class="min-w-0">
						<p class="font-display text-xs font-bold tracking-wide text-muted uppercase">{(article.teams ?? []).join(', ') || t('portal.news')}{#if article.published_at} · {formatDay(article.published_at)}{/if}</p>
						<h3 class="font-display text-lg leading-tight font-extrabold text-ink uppercase">{translation(article).title}</h3>
					</div>
					<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
				</div>
			</a>
		{/each}
	</div>
{:else}
	<p class="card card-body text-sm text-muted">{t('portal.no_news')}</p>
{/if}
<Sheet open={!!selected} title={selected ? translation(selected).title : t('portal.news')} onclose={() => void goto(home ? path : path + '?tab=news', { reset: false, replace: true })}>
	{#if selected}
		{@const lead = photo(selected)}
		{#if lead}<img class="aspect-video w-full object-cover" src={lead.url} style:object-position={lead.object_position} alt="" />{/if}
		<article class="grid gap-3 p-4">
			<h2 class="font-display text-2xl font-extrabold text-ink uppercase">{translation(selected).title}</h2>
			<div class="prose max-w-none text-sm">{@html translation(selected).body}</div>
		</article>
	{/if}
</Sheet>
