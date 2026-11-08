<script lang="ts">
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import type { NewsListItem } from '#lib/news.ts';

	/** The latest published club news (real data, newest publish date first). Publishers also see how many items wait for review. */
	let { items, pending = 0 }: { items: NewsListItem[]; pending?: number } = $props();
	const day = (iso: string) => new Intl.DateTimeFormat(getLang(), { day: 'numeric', month: 'short' }).format(new Date(iso));
</script>

<div class="card card-body h-full gap-2">
	<div class="flex items-center justify-between gap-2">
		<h2 class="card-title">{t('overview.news.title')}</h2>
		<a class="btn btn-outline btn-sm" href="/news">{t('overview.news.view_all')}</a>
	</div>
	{#if pending > 0}
		<a class="alert alert-warning py-2 text-sm" href="/news?tab=review">{t('overview.news.pending', { n: pending })}</a>
	{/if}
	{#if items.length}
		<ul class="divide-y divide-rule">
			{#each items as n (n.id)}
				<li>
					<a class="flex items-baseline justify-between gap-4 py-2.5 hover:text-brand" href="/news/{n.id}">
						<span class="min-w-0 truncate font-semibold text-ink">{n.title}</span>
						<span class="shrink-0 font-mono text-xs text-muted">{day(n.published_at ?? n.created)}</span>
					</a>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="py-6 text-center text-sm text-muted">{t('overview.news.empty')}</p>
	{/if}
</div>
