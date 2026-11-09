<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import DiscountFields from '#lib/components/shop/DiscountFields.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { DiscountIn } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	// svelte-ignore state_referenced_locally
	let v = $state<DiscountIn>($state.snapshot(data.initial));
	let tried = $state(false);
	let busy = $state(false);
	const fields = $derived((form && 'fields' in form ? form.fields : {}) as Record<string, string>);
	const errorText = $derived(form && 'status' in form && form.status ? form.message || (form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed')) : '');
	const valid = () => !!v.name.trim() && !!v.code.trim() && Number(v.discount_amount) > 0 && (v.discount_type !== 'percentage' || Number(v.discount_amount) <= 100) && !(v.valid_from && v.valid_until && v.valid_until < v.valid_from);

	usePageHeader(() => ({ title: t('shop.disc.title_new'), context: t('shop.nav.discounts'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/discounts"><ArrowLeft size={16} /> {t('shop.disc.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.disc.title_new'))}</title></svelte:head>

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		if (!valid()) {
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
	{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<section class="card card-body gap-3 xl:col-span-2">
			<p class="text-sm text-muted">{t('shop.disc.new_help')}</p>
			<DiscountFields bind:v {fields} {tried} />
		</section>
		<section class="card card-body gap-3 xl:sticky xl:top-20">
			<div class="flex flex-wrap gap-2">
				<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Save size={16} /> {t('shop.disc.create')}</button>
				<a class="btn btn-outline" href="/shop/discounts">{t('common.cancel')}</a>
			</div>
		</section>
	</div>
</form>
