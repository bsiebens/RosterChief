<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import VoucherFields from '#lib/components/shop/VoucherFields.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { VoucherIn } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	// svelte-ignore state_referenced_locally
	let v = $state<VoucherIn>($state.snapshot(data.initial));
	let tried = $state(false);
	let busy = $state(false);
	const fields = $derived((form && 'fields' in form ? form.fields : {}) as Record<string, string>);
	const errorText = $derived(form && 'status' in form && form.status ? form.message || (form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed')) : '');
	const valid = () => Number(v.amount) > 0 && !!v.expiry_date && (v.count ?? 1) >= 1 && (v.count ?? 1) <= 200;

	usePageHeader(() => ({ title: t('shop.vch.title_new'), context: t('shop.nav.vouchers'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/vouchers"><ArrowLeft size={16} /> {t('shop.vch.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.vch.title_new'))}</title></svelte:head>

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
			<p class="text-sm text-muted">{t('shop.vch.new_help')}</p>
			<VoucherFields bind:v {fields} {tried} bulk />
		</section>
		<section class="card card-body gap-3 xl:sticky xl:top-20">
			<p class="text-sm font-semibold">{(v.count ?? 1) > 1 ? t('shop.vch.will_create_n', { n: v.count ?? 1 }) : t('shop.vch.will_create_one')}</p>
			<div class="flex flex-wrap gap-2">
				<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Save size={16} /> {t('shop.vch.create')}</button>
				<a class="btn btn-outline" href="/shop/vouchers">{t('common.cancel')}</a>
			</div>
		</section>
	</div>
</form>
