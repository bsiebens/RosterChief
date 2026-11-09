<script lang="ts">
	import { ImagePlus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { ProductDetail } from '#lib/shop.ts';

	/** The product image: upload (replaces) and remove, straight from the browser. */
	let { productId, url = $bindable(), name }: { productId: string; url: string | null; name: string } = $props();
	let busy = $state(false);
	let errorMsg = $state('');
	let input: HTMLInputElement | undefined = $state();

	async function upload() {
		const file = input?.files?.[0];
		if (!file) return;
		busy = true;
		errorMsg = '';
		const form = new FormData();
		form.append('image', file);
		const res = await api<ProductDetail>('POST', `/shop/products/${productId}/image`, { form });
		busy = false;
		if (input) input.value = '';
		if (res.ok && res.data) url = res.data.image_url;
		else errorMsg = res.fields.image || res.fields.__all__ || res.message || t('common.error.generic');
	}
	async function remove() {
		busy = true;
		errorMsg = '';
		const res = await api<ProductDetail>('DELETE', `/shop/products/${productId}/image`);
		busy = false;
		if (res.ok) url = null;
		else errorMsg = res.message || t('common.error.generic');
	}
</script>

<div class="grid gap-3">
	{#if errorMsg}<Alert kind="error">{errorMsg}</Alert>{/if}
	{#if url}
		<img src={url} alt={name} class="aspect-square w-full rounded-lg border border-line object-cover" />
	{:else}
		<div class="flex aspect-square w-full items-center justify-center rounded-lg border border-dashed border-line text-sm text-muted">{t('shop.image.none')}</div>
	{/if}
	<input bind:this={input} type="file" accept="image/png,image/jpeg,image/webp" class="hidden" onchange={upload} />
	<div class="flex flex-wrap gap-2">
		<button class="btn btn-outline btn-sm gap-2" type="button" disabled={busy} onclick={() => input?.click()}><ImagePlus size={14} /> {url ? t('shop.image.replace') : t('shop.image.upload')}</button>
		{#if url}<button class="btn btn-outline btn-error btn-sm gap-2" type="button" disabled={busy} onclick={remove}><Trash2 size={14} /> {t('shop.image.remove')}</button>{/if}
	</div>
	<p class="text-xs text-muted">{t('shop.image.help')}</p>
</div>
