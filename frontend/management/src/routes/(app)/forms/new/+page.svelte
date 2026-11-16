<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import FormDetailsFields from '#lib/components/forms/FormDetailsFields.svelte';
	import type { FormIn } from '#lib/forms.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	// svelte-ignore state_referenced_locally
	let v = $state<FormIn>($state.snapshot(data.initial));
	let tried = $state(false);
	let busy = $state(false);
	const fields = $derived(form?.fields ?? {});
	const errorText = $derived(form?.status ? form.message || (form.status === 403 ? t('forms.err.forbidden') : t('forms.err.failed')) : '');

	usePageHeader(() => ({ title: t('forms.title_new'), context: t('forms.context'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/forms"><ArrowLeft size={16} /> {t('forms.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('forms.title_new'))}</title></svelte:head>

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		if (!v.title.trim()) {
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
	{#if errorText}<Alert kind="error" title={t('forms.save_failed')}>{errorText}</Alert>{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<section class="card card-body gap-3 xl:col-span-2">
			<p class="text-sm text-muted">{t('forms.new_help')}</p>
			<FormDetailsFields bind:v {fields} {tried} />
		</section>
		<div class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body gap-3">
				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('forms.create')}</button>
					<a class="btn btn-outline w-full" href="/forms">{t('common.cancel')}</a>
				</div>
			</section>
		</div>
	</div>
</form>
