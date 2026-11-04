<script lang="ts">
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { mockNews } from '#lib/overview.ts';

	/** Sample content until the news module exists. */
	const news = mockNews().slice(0, 5); // the five latest
	const day = (iso: string) => new Intl.DateTimeFormat(getLang(), { day: 'numeric', month: 'short' }).format(new Date(iso));
</script>

<div class="card card-body sample-stripes h-full gap-2" title={t('overview.sample_hint')}>
	<div class="flex items-center justify-between gap-2">
		<h2 class="card-title">{t('overview.news.title')}</h2>
		<button class="btn btn-outline btn-sm" type="button" disabled>{t('overview.news.view_all')}</button>
	</div>
	<ul class="divide-y divide-rule">
		{#each news as n (n.id)}
			<li class="flex items-baseline justify-between gap-4 py-2.5">
				<span class="min-w-0 truncate font-semibold text-ink">{n.title}</span>
				<span class="shrink-0 font-mono text-xs text-muted">{day(n.date)}</span>
			</li>
		{/each}
	</ul>
</div>
