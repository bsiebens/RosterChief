<script lang="ts">
	import { SearchX } from '@lucide/svelte';
	import { page } from '$app/state';
	import PublicShell from '#lib/components/registration/PublicShell.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/** Any failure under /tournaments: a missing tournament, a wrong link or a club without the feature is the same clean "not found". */
	const club = $derived(page.data.mode === 'club' ? page.data.branding : null);
	const notFound = $derived(page.status === 404);
</script>

<svelte:head>
	<title>{notFound ? t('tp.notfound.title') : t('tp.error.title')}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<PublicShell title={t('tp.list.title')} kicker={t('tp.kicker')} {club}>
	<div class="card card-body items-center gap-2 py-10 text-center" role="status">
		<SearchX size={32} class="text-dim" aria-hidden="true" />
		<p class="font-display text-xl font-bold text-ink">{notFound ? t('tp.notfound.title') : page.status === 429 ? t('reg.err.rate_limited') : t('tp.error.title')}</p>
		{#if notFound}<p class="text-muted">{t('tp.notfound.body')}</p>{/if}
	</div>
</PublicShell>
