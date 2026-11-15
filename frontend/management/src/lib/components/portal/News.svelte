<script lang="ts">
	import { ChevronRight, Newspaper } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { formatDay } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { mediaPath } from '#lib/media.ts';
	import type { PortalArticle } from '#lib/portal.ts';
	import Sheet from './Sheet.svelte';

	let { articles, home = false, path = '/parent' }: { articles: PortalArticle[]; home?: boolean; path?: string } = $props();
	const shown = $derived(home ? articles.slice(0, 4) : articles);
	const selected = $derived(articles.find((article) => article.id === page.url.searchParams.get('article')));
	const translation = (article: PortalArticle) => article.translations[getLang() === 'nl' ? 'nl' : 'en'];
	const photo = (article: PortalArticle) => article.photos.find((image) => image.is_main) ?? article.photos[0];
</script>

{#if home}
	<div class="mb-2 flex items-center justify-between gap-2">
		<h2 class="card-title"><Newspaper size={16} aria-hidden="true" />{t('portal.news')}</h2>
		<a class="link-more" href={path + '?tab=news'}>{t('overview.news.view_all')} →</a>
	</div>
{/if}
{#if shown.length}
	<div class={home ? 'grid gap-3 md:grid-cols-2' : 'grid gap-4 sm:grid-cols-2 2xl:grid-cols-3'}>
		{#each shown as article (article.id)}
			{@const lead = photo(article)}
			<a class={home ? 'card group flex gap-3 p-3 hover:border-club' : 'card group block overflow-hidden hover:border-club'} href={path + (home ? '?' : '?tab=news&') + 'article=' + article.id} data-sveltekit-reset="false">
				{#if lead}
					<img class={home ? 'h-[4.5rem] w-24 shrink-0 rounded object-cover' : 'aspect-3/1 w-full object-cover'} src={mediaPath(lead.url)} style:object-position={lead.object_position} alt="" loading="lazy" />
				{:else}
					<div class="grid place-items-center bg-subhead text-dim {home ? 'h-[4.5rem] w-24 shrink-0 rounded' : 'aspect-3/1'}"><Newspaper size={home ? 22 : 28} aria-hidden="true" /></div>
				{/if}
				<div class="flex min-w-0 flex-1 items-center justify-between gap-2 {home ? '' : 'px-3.5 py-3'}">
					<div class="min-w-0">
						<p class="truncate font-display text-xs font-bold tracking-wide text-muted uppercase">{(article.teams ?? []).join(', ') || t('portal.news')}{#if article.published_at} · {formatDay(article.published_at)}{/if}</p>
						<h3 class="font-display text-base leading-tight font-extrabold text-ink uppercase {home ? 'line-clamp-2' : 'text-lg'}">{translation(article).title}</h3>
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
		{#if lead}<img class="aspect-video w-full object-cover" src={mediaPath(lead.url)} style:object-position={lead.object_position} alt="" />{/if}
		<article class="grid gap-3 p-4">
			<h2 class="font-display text-2xl font-extrabold text-ink uppercase">{translation(selected).title}</h2>
			<div class="prose max-w-none text-sm">{@html translation(selected).body}</div>
		</article>
	{/if}
</Sheet>
