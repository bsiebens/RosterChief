<script lang="ts">
	import { ArrowLeft, CircleCheck } from '@lucide/svelte';
	import FormResponse from '#lib/components/forms/FormResponse.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: data.form.title }));
</script>

<svelte:head><title>{pageTitle(data.form.title)}</title></svelte:head>

<div class="grid w-full max-w-3xl min-w-0 gap-4">
	<a class="btn btn-outline justify-self-start gap-2" href="/parent?tab=forms"><ArrowLeft size={16} aria-hidden="true" />{t('portal.forms')}</a>
	<p class="text-sm font-semibold">{data.form.member}</p>
	{#if data.form.limit !== null && data.form.submitted >= data.form.limit}
		<div class="card card-body">
			<h2 class="card-title"><CircleCheck size={16} aria-hidden="true" />{t('pubform.thanks.title')}</h2>
		</div>
	{:else}
		<FormResponse form={data.form} endpoint={data.endpoint} />
	{/if}
</div>
