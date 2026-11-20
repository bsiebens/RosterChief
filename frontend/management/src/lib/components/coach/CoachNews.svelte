<script lang="ts">
	import { Newspaper } from '@lucide/svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { formatDay } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { mediaPath } from '#lib/media.ts';
	import { coachPill, type NewsListItem } from '#lib/news.ts';
	import Alert from '#lib/components/Alert.svelte';
	import type { PortalArticle } from '#lib/portal.ts';
	import Sheet from '#lib/components/portal/Sheet.svelte';

	let { items, articles, mine = false, canFilter = false }: { items: NewsListItem[]; articles: PortalArticle[]; mine?: boolean; canFilter?: boolean } = $props();
	const notice = $derived(page.url.searchParams.get('notice'));
	const notices = ['saved', 'submitted', 'published', 'scheduled', 'unpublished', 'sent_back', 'deleted'];
	const chip = (own: boolean) => `/coach?tab=news${own ? '&mine=1' : ''}`;
	const lead = $derived(items[0]);
	const rest = $derived(items.slice(1));
	const selected = $derived(articles.find((article) => article.id === page.url.searchParams.get('article')));
	const translation = (article: PortalArticle) => article.translations[getLang() === 'nl' ? 'nl' : 'en'];
	const photo = (article: PortalArticle) => article.photos.find((image) => image.is_main) ?? article.photos[0];
	const pill = coachPill;
	const when = (item: NewsListItem) => formatDay(item.published_at ?? item.created);
	const meta = (item: NewsListItem) => [when(item), (item.teams ?? []).map((team) => team.name).join(', ')].filter(Boolean).join(' · ');
	// A published article opens in the reading drawer; a draft goes to the editor, which only its writer and publishers can open.
	const href = (item: NewsListItem) => (item.status === 'published' && articles.some((article) => article.id === item.id) ? `/coach?tab=news&article=${item.id}` : item.can_edit ? `/coach/news/${item.id}` : null);
</script>

{#snippet card(item: NewsListItem, big: boolean)}
	{@const [label, tone] = pill(item)}
	{@const link = href(item)}
	<svelte:element this={link ? 'a' : 'div'} class="card block overflow-hidden {link ? 'hover:border-club' : ''} {big ? '' : 'flex gap-3 p-3'}" href={link ?? undefined} data-sveltekit-reset="false">
		{#if item.main_photo_url}
			<img class={big ? 'aspect-video max-h-72 w-full object-cover' : 'h-[4.25rem] w-[5.25rem] shrink-0 rounded object-cover'} src={mediaPath(item.main_photo_url)} alt="" loading="lazy" />
		{:else}
			<div class="grid place-items-center bg-subhead text-dim {big ? 'aspect-video max-h-72 w-full' : 'h-[4.25rem] w-[5.25rem] shrink-0 rounded'}"><Newspaper size={big ? 36 : 22} aria-hidden="true" /></div>
		{/if}
		<div class="grid min-w-0 content-center gap-1.5 {big ? 'p-4' : ''}">
			<div class="flex flex-wrap items-center gap-2"><span class="badge badge-sm {tone}">{t(label)}</span>{#if big}<span class="font-mono text-[11px] tracking-[.12em] text-muted uppercase">{meta(item)}</span>{/if}</div>
			{#if big}<h2 class="font-display text-2xl leading-none font-extrabold text-ink uppercase">{item.title}</h2>{:else}<h3 class="line-clamp-2 text-sm font-semibold text-ink">{item.title}</h3><p class="truncate text-xs text-muted">{meta(item)}</p>{/if}
		</div>
	</svelte:element>
{/snippet}

{#if notice && notices.includes(notice)}
	<div class="mb-3 flex items-start gap-2"><div class="min-w-0 flex-1"><Alert kind="success">{t(`coach.news_notice_${notice}`)}</Alert></div><a class="btn btn-outline btn-square min-h-11" href="/coach?tab=news{mine ? '&mine=1' : ''}" data-sveltekit-replacestate aria-label={t('common.close')}>×</a></div>
{/if}
{#if canFilter}
	<div class="mb-3 flex gap-2" role="group" aria-label={t('shell.nav.news')}>
		<button type="button" class="portal-person compact text" aria-pressed={!mine} onclick={() => void goto(chip(false), { replaceState: true })}>{t('coach.news_all')}</button>
		<button type="button" class="portal-person compact text" aria-pressed={mine} onclick={() => void goto(chip(true), { replaceState: true })}>{t('coach.news_mine')}</button>
	</div>
{/if}
{#if items.length}
	<div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
		{@render card(lead, true)}
		<div class="grid gap-3">{#each rest as item (item.id)}{@render card(item, false)}{/each}</div>
	</div>
{:else}
	<p class="card card-body text-sm text-muted">{t('portal.no_news')}</p>
{/if}

<Sheet wide open={!!selected} title={selected ? translation(selected).title : t('portal.news')} onclose={() => void goto('/coach?tab=news', { reset: false, replace: true })}>
	{#if selected}
		{@const cover = photo(selected)}
		{#if cover}<img class="aspect-video max-h-96 w-full object-cover" src={mediaPath(cover.url)} style:object-position={cover.object_position} alt="" />{/if}
		<article class="mx-auto grid max-w-2xl gap-4 px-6 py-6">
			<p class="font-mono text-[11px] tracking-[.14em] text-muted uppercase">{(selected.teams ?? []).join(', ') || t('portal.news')}{#if selected.published_at} · {formatDay(selected.published_at)}{/if}</p>
			<h2 class="font-display text-4xl leading-none font-extrabold text-ink uppercase">{translation(selected).title}</h2>
			<div class="news-body max-w-none text-base leading-relaxed text-slate">{@html translation(selected).body}</div>
			{#each selected.photos.filter((image) => image !== cover) as image (image.url)}
				<img class="w-full rounded-box object-cover" src={mediaPath(image.url)} style:object-position={image.object_position} alt="" loading="lazy" />
			{/each}
		</article>
	{/if}
</Sheet>
