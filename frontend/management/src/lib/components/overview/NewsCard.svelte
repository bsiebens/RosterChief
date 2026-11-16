<script lang="ts">
	import { ChevronRight, Newspaper } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { NewsListItem } from '#lib/news.ts';

	/** The latest published club news (real data, newest publish date first). Publishers also see how many items wait for review. */
	let { items, pending = 0 }: { items: NewsListItem[]; pending?: number } = $props();
	const day = (iso: string) => new Intl.DateTimeFormat(getLang(), { day: 'numeric', month: 'short' }).format(new Date(iso));
	const audience = (item: NewsListItem) => {
		if (!item.team_ids.length) return t('news.club_wide');
		return item.teams?.map((team) => team.name).join(', ') || t('news.teams_n', { n: item.team_ids.length });
	};
</script>

<div class="card @container min-w-0">
	<div class="card-h"><Newspaper size={17} class="shrink-0" aria-hidden="true" /><h2>{t('overview.news.title')}</h2><a class="ml-auto text-sm font-semibold text-club-dark hover:underline" href="/news">{t('overview.news.view_all')} →</a></div>
	<div class="grid gap-2 px-5 py-3">
	{#if pending > 0}
		<a class="alert alert-warning py-2 text-sm" href="/news?tab=review">{t('overview.news.pending', { n: pending })}</a>
	{/if}
	{#if items.length}
		<ul class="divide-y divide-rule">
			{#each items as n (n.id)}
				<li>
					<a class="group grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-3 py-2.5 @md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)_5rem_6rem]" href="/news/{n.id}">
						<span class="col-start-1 row-start-1 truncate font-semibold text-ink group-hover:text-brand">{n.title}</span>
						<span class="col-start-1 row-start-2 wrap-break-word text-xs text-muted @md:col-start-2 @md:row-start-1 @md:text-sm">{audience(n)}</span>
						<span class="col-start-2 row-span-2 row-start-1 justify-self-end font-mono text-xs whitespace-nowrap text-muted @md:col-start-3 @md:row-span-1">{day(n.published_at ?? n.created)}</span>
						<span class="btn btn-outline btn-sm col-start-3 row-span-2 row-start-1 justify-self-end gap-1 @md:col-start-4 @md:row-span-1" aria-hidden="true">{t('news.open')} <ChevronRight size={14} /></span>
					</a>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="py-6 text-center text-sm text-muted">{t('overview.news.empty')}</p>
	{/if}
	</div>
</div>
