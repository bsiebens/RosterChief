<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ProductFields from '#lib/components/shop/ProductFields.svelte';
	import ProductVariantsEditor from '#lib/components/shop/ProductVariantsEditor.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { productPayload, type ProductIn, type VariantIn } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	// svelte-ignore state_referenced_locally
	let v = $state<ProductIn>($state.snapshot(data.initial));
	// A variant can name its own team on membership products outside the Volunteer category.
	const teamsForVariants = $derived(v.product_type === 'membership' && data.categories.find((c) => c.id === v.category_id)?.system_key !== 'volunteer' ? data.teams : []);
	// Switching to a product that has no registration team (not a membership, or the Volunteer category) drops the variant teams too.
	$effect(() => {
		if (!teamsForVariants.length) for (const row of variants) if (row.team_id) row.team_id = null;
	});
	let variants = $state<VariantIn[]>([]);
	let tried = $state(false);
	let busy = $state(false);
	const fields = $derived((form && 'fields' in form ? form.fields : {}) as Record<string, string>);
	const errorText = $derived(form && 'status' in form && form.status ? form.message || (form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed')) : '');

	usePageHeader(() => ({ title: t('shop.product.title_new'), context: t('shop.nav.products'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/products"><ArrowLeft size={16} /> {t('shop.product.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.product.title_new'))}</title></svelte:head>

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		if (!v.name.trim() || v.price === '' || (v.product_type !== 'membership' && Number(v.price) < 0)) {
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
	<input type="hidden" name="payload" value={JSON.stringify({ ...productPayload(v), variants })} />
	{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.card.details')}</h2>
				<ProductFields bind:v categories={data.categories} seasons={data.seasons} teams={data.teams} {fields} {tried} />
			</section>
			<section class="card card-body gap-3">
				<header class="grid gap-0.5">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.card.variants')}</h2>
					<p class="text-xs text-muted">{t('shop.card.variants_desc')}</p>
				</header>
				<ProductVariantsEditor bind:variants membership={v.product_type === 'membership'} teams={teamsForVariants} />
			</section>
		</div>
		<section class="card card-body gap-3 xl:sticky xl:top-20">
			<p class="text-sm text-muted">{t('shop.product.new_help')}</p>
			<p class="text-xs text-muted">{t('shop.attach.hint')}</p>
			<div class="grid gap-2">
				<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('shop.product.create')}</button>
				<a class="btn btn-outline w-full" href="/shop/products">{t('common.cancel')}</a>
			</div>
		</section>
	</div>
</form>
