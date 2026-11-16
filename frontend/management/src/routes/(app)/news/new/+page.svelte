<script lang="ts">
	import { enhance } from '$app/forms';
	import { Users, ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import NewsFields from '#lib/components/news/NewsFields.svelte';
	import NewsPreview from '#lib/components/news/NewsPreview.svelte';
	import PanelTabs from '#lib/components/news/PanelTabs.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { NewsIn } from '#lib/news.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	// svelte-ignore state_referenced_locally
	let v = $state<NewsIn>($state.snapshot(data.initial));
	let tried = $state(false);
	let busy = $state(false);
	let tab = $state('edit');
	const tabId = $props.id();
	const tabs = $derived([{ key: 'edit', label: t('news.tab.edit') }, { key: 'preview', label: t('news.tab.preview') }]);

	const fields = $derived(form?.fields ?? {});
	const errorText = $derived(form?.status ? form.message || (form.status === 403 ? t('news.err.forbidden') : t('news.err.failed')) : '');
	const teamNames = $derived(data.teams.filter((x) => v.team_ids.includes(x.id)).map((x) => x.short_name || x.name));

	usePageHeader(() => ({ title: t('news.title_new'), context: t('news.context'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/news"><ArrowLeft size={16} /> {t('news.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('news.title_new'))}</title></svelte:head>

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		if (!v.title.trim() || !v.body.trim()) {
			tried = true;
			cancel();
			return;
		}
		busy = true;
		return async ({ update }) => {
			await update({ reset: false });
			busy = false;
		};
	}}>
	<input type="hidden" name="payload" value={JSON.stringify(v)} />
	{#if errorText}<Alert kind="error" title={t('news.save_failed')}>{errorText}</Alert>{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<PanelTabs {tabs} bind:active={tab} label={t('news.tab.label')} idBase={tabId} />
			<div id="{tabId}-panel-edit" role="tabpanel" aria-labelledby="{tabId}-tab-edit" hidden={tab !== 'edit'}>
				<NewsFields bind:v teams={data.teams} {fields} {tried} />
			</div>
			<div id="{tabId}-panel-preview" role="tabpanel" aria-labelledby="{tabId}-tab-preview" hidden={tab !== 'preview'}>
				<NewsPreview title={v.title} body={v.body} title_en={v.title_en} body_en={v.body_en} />
			</div>
		</div>
		<div class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body gap-3">
				<div class="grid gap-1.5">
					<p class="font-display text-xs font-bold tracking-[.08em] text-muted uppercase flex items-center gap-2"><Users size={16} class="shrink-0" aria-hidden="true" /> {t('news.card.audience')}</p>
					{#if teamNames.length}
						<div class="flex flex-wrap gap-1.5">{#each teamNames as name (name)}<span class="badge badge-neutral">{name}</span>{/each}</div>
					{:else}
						<span class="font-display text-xs font-bold tracking-[.14em] text-club uppercase">{t('news.club_wide')}</span>
					{/if}
				</div>
				<p class="text-sm text-muted">{t('news.photos.save_first')}</p>
				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('news.save_draft')}</button>
					<a class="btn btn-outline w-full" href="/news">{t('common.cancel')}</a>
				</div>
			</section>
		</div>
	</div>
</form>
