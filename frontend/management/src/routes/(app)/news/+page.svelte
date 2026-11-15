<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ChevronRight, Image as ImageIcon, Newspaper, Plus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FilterTabs from '#lib/components/FilterTabs.svelte';
	import ListFilterBar from '#lib/components/ListFilterBar.svelte';
	import NewsStatusBadge from '#lib/components/news/NewsStatusBadge.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { newsHref } from '#lib/news.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const publisher = $derived(data.me.can_publish_news);
	const href = (patch: Partial<{ q: string; tab: string; mine: boolean; offset: number }>) => {
		const f = { ...data.filters, ...('offset' in patch ? {} : { offset: 0 }), ...patch };
		const sp = new URLSearchParams();
		if (f.q) sp.set('q', f.q);
		if (f.tab !== 'all') sp.set('tab', f.tab);
		if (f.mine) sp.set('mine', '1');
		if (f.offset) sp.set('offset', String(f.offset));
		const qs = sp.toString();
		return qs ? `?${qs}` : '?';
	};
	const navigate = (to: string) => goto(to, { reset: false });
	const filtered = $derived(!!data.filters.q || data.filters.mine || data.filters.tab !== 'all');
	const tabList = $derived([
		{ key: 'all', label: t('news.tab.all'), count: data.counts.all },
		...(publisher ? [{ key: 'review', label: t('news.tab.review'), count: data.counts.review, warn: true }] : []),
		{ key: 'draft', label: t('news.tab.draft'), count: data.counts.draft },
		{ key: 'published', label: t('news.tab.published'), count: data.counts.published }
	]);
	const teamName = (id: string) => data.teams.find((x) => x.id === id);
	const audience = (ids: string[]) => {
		if (!ids.length) return t('news.club_wide');
		const names = ids.map((id) => teamName(id)?.short_name || teamName(id)?.name).filter(Boolean) as string[];
		return names.length && names.length <= 3 ? names.join(', ') : t('news.teams_n', { n: ids.length });
	};
	const deleted = $derived(page.url.searchParams.get('deleted') === '1');

	usePageHeader(() => ({ title: t('news.title'), context: data.count === 1 ? t('news.count_one') : t('news.count', { n: data.count }), actions }));
</script>

{#snippet actions()}
	{#if data.me.can_add_news}<a class="btn btn-primary gap-2" href="/news/new"><Plus size={16} /> {t('news.add')}</a>{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('news.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<ListFilterBar
		value={data.filters.q}
		searchLabel={t('news.search')}
		searchPlaceholder={t('news.search_placeholder')}
		onsearch={(q) => goto(href({ q }), { replace: true, reset: false })}
		showClear={filtered}
		onclear={() => navigate('?')}>
		<label class="flex cursor-pointer items-center gap-2 text-sm">
			<input type="checkbox" class="checkbox checkbox-sm" checked={data.filters.mine} onchange={() => navigate(href({ mine: !data.filters.mine }))} />
			{t('news.mine')}
		</label>
		{#snippet tabs()}
			<FilterTabs tabs={tabList} active={data.filters.tab} label={t('news.tabs')} hrefFor={(key) => href({ tab: key })} onnavigate={navigate} />
		{/snippet}
	</ListFilterBar>

	<div class="grid gap-4 px-6 py-4">
		{#if deleted}<Alert kind="success">{t('news.deleted')}</Alert>{/if}
		{#if publisher && data.reviewCount && data.filters.tab !== 'review'}
			<Alert kind="warning">
				{data.reviewCount === 1 ? t('news.review_banner_one') : t('news.review_banner', { n: data.reviewCount })}
				<a class="link font-semibold underline" href={href({ tab: 'review' })}>{t('news.review_banner_cta')}</a>
			</Alert>
		{/if}

		{#if data.error}
			<Alert kind="error">{t('common.error.generic')}</Alert>
		{:else if !data.rows.length}
			<div class="card card-body items-center py-10 text-center text-muted">
				<Newspaper size={28} />
				<p>{filtered ? t('news.empty_filtered') : t('news.empty')}</p>
			</div>
		{:else}
			<div class="card overflow-x-auto">
				<table class="table">
					<thead>
						<tr><th>{t('news.col.post')}</th><th class="hidden lg:table-cell">{t('news.col.audience')}</th><th class="hidden xl:table-cell">{t('news.col.author')}</th><th>{t('news.col.date')}</th><th></th></tr>
					</thead>
					<tbody>
						{#each data.rows as n (n.id)}
							<tr class="hover:bg-paper {n.status === 'pending_review' && publisher ? 'bg-warn-bg' : ''}">
								<td>
									<div class="flex items-center gap-3">
										<span class="grid size-12 shrink-0 place-items-center overflow-hidden rounded-lg border border-line bg-paper text-dim" aria-hidden="true">
											{#if n.main_photo_url}<img src={n.main_photo_url} alt="" class="size-full object-cover" />{:else}<ImageIcon size={16} />{/if}
										</span>
										<div class="min-w-0">
											<a class="link font-semibold" href={newsHref(n.id)}>{n.title}</a>
											<div class="mt-0.5 flex flex-wrap items-center gap-2"><NewsStatusBadge item={n} small /><span class="font-mono text-[11px] text-dim">{t(`news.visibility.short.${n.visibility}`)}</span></div>
										</div>
									</div>
								</td>
								<td class="hidden text-sm text-muted lg:table-cell">{audience(n.team_ids)}</td>
								<td class="hidden text-sm text-muted xl:table-cell">{n.created_by?.name ?? t('common.none')}</td>
								<td class="font-mono text-xs whitespace-nowrap text-muted">{formatDateTime(n.status === 'published' && n.published_at ? n.published_at : n.created)}</td>
								<td class="text-right whitespace-nowrap">
									<a class="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-rule hover:text-ink" href={newsHref(n.id)} title={n.can_edit ? (n.status === 'pending_review' && publisher ? t('news.review') : t('common.edit')) : t('news.open')} aria-label={n.can_edit ? (n.status === 'pending_review' && publisher ? t('news.review_named', { title: n.title }) : t('news.edit_named', { title: n.title })) : t('news.open')}><ChevronRight size={18} /></a>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Pager count={data.count} offset={data.filters.offset} limit={data.pageSize} href={(o) => href({ offset: o })} />
		{/if}
	</div>
</div>
