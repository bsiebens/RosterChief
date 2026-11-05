<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { ArrowLeft, ArrowRight, Download, Shirt, TriangleAlert } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDateTime } from '#lib/events/types.ts';

	let { data } = $props();
	const nothingAvailable = $derived(data.rows.length > 0 && data.rows.every((r) => !r.anyEnabled));
	usePageHeader(() => ({ title: t('calimport.title'), context: t('calimport.context'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/calendar"><ArrowLeft size={16} /> {t('calimport.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('calimport.title'))}</title></svelte:head>

<div class="grid gap-4">
	<p class="max-w-4xl text-sm text-muted">{t('calimport.intro')}</p>
	{#if nothingAvailable}<Alert kind="warning" title={t('calimport.none_title')}>{t('calimport.none_body')}</Alert>{/if}

	{#if data.rows.length}
		<ul class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
			{#each data.rows as { team, sources } (team.id)}
				<li>
					<a class="card card-body h-full gap-3 transition-colors hover:border-club" href="/teams/{team.id}/import">
						<div class="flex items-center gap-3">
							<span class="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-club/10 text-club-dark"><Shirt size={18} aria-hidden="true" /></span>
							<h2 class="min-w-0 flex-1 truncate font-display text-lg font-extrabold tracking-[.03em] text-ink uppercase">{team.name}</h2>
						</div>
						{#if sources === null}
							<p class="flex items-center gap-1.5 text-sm text-muted"><TriangleAlert size={14} aria-hidden="true" /> {t('calimport.unknown')}</p>
						{:else if sources.length}
							<ul class="grid gap-1 text-sm">
								{#each sources.slice(0, 3) as s (s.id)}
									<li class="flex items-baseline justify-between gap-2"><span class="font-semibold text-ink">{s.source}{#if s.competition_label} <span class="font-normal text-muted">· {s.competition_label}</span>{/if}</span><span class="font-mono text-xs text-muted">{s.last_imported ? formatDateTime(s.last_imported) : t('calimport.never')}</span></li>
								{/each}
								{#if sources.length > 3}<li class="text-xs text-muted">{t('calimport.more', { n: sources.length - 3 })}</li>{/if}
							</ul>
						{:else}
							<p class="text-sm text-muted">{t('calimport.no_sources')}</p>
						{/if}
						<span class="mt-auto flex items-center gap-1 text-sm font-semibold text-club-dark"><Download size={14} aria-hidden="true" /> {sources?.length ? t('calimport.manage') : t('calimport.setup')} <ArrowRight size={14} aria-hidden="true" /></span>
					</a>
				</li>
			{/each}
		</ul>
	{:else}
		<div class="card card-body items-center py-10 text-center text-muted"><Shirt size={28} /><p>{t('calimport.no_teams')}</p></div>
	{/if}
</div>
