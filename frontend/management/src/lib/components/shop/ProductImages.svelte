<script lang="ts">
	import { ArrowLeft, ArrowRight, ImagePlus, Pencil, Star, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { ProductDetail, ProductImageItem } from '#lib/shop.ts';

	/** The product pictures: gallery with a main picture, order, description and delete, straight from the browser. */
	let { productId, name, images = $bindable() }: { productId: string; name: string; images: ProductImageItem[] } = $props();

	const MAX_COUNT = 12;
	const MAX_BYTES = 10 * 1024 * 1024;
	let busy = $state(false);
	let errorMsg = $state('');
	let input: HTMLInputElement | undefined = $state();
	let confirmId = $state<string | null>(null);
	const base = $derived(`/shop/products/${productId}/images`);
	const fail = (r: { message?: string; fields: Record<string, string> }) => (errorMsg = r.fields.images || r.fields.__all__ || r.message || t('common.error.generic'));

	async function call(method: string, path: string, body?: unknown) {
		busy = true;
		errorMsg = '';
		confirmId = null;
		const res = await api<ProductDetail>(method, path, body === undefined ? {} : { body });
		busy = false;
		if (res.ok && res.data) images = res.data.images ?? [];
		else fail(res);
		return res.ok;
	}

	async function upload() {
		const files = input?.files ? [...input.files] : [];
		if (input) input.value = '';
		if (!files.length) return;
		errorMsg = '';
		if (images.length + files.length > MAX_COUNT) return void (errorMsg = t('shop.image.err.count', { max: MAX_COUNT }));
		const big = files.find((f) => f.size > MAX_BYTES);
		if (big) return void (errorMsg = t('shop.image.err.size', { name: big.name }));
		busy = true;
		confirmId = null;
		const form = new FormData();
		for (const f of files) form.append('images', f);
		const res = await api<ProductDetail>('POST', base, { form });
		busy = false;
		if (res.ok && res.data) images = res.data.images ?? [];
		else fail(res);
	}

	function move(i: number, d: number) {
		const ids = images.map((p) => p.id);
		const [id] = ids.splice(i, 1);
		ids.splice(i + d, 0, id);
		return call('PUT', `${base}/order`, { ids });
	}
	const remove = (p: ProductImageItem) => {
		if (confirmId !== p.id) {
			confirmId = p.id;
			return;
		}
		return call('DELETE', `${base}/${p.id}`);
	};

	let altOpen = $state(false);
	let altFor = $state<ProductImageItem | null>(null);
	let altText = $state('');
	function openAlt(p: ProductImageItem) {
		altFor = p;
		altText = p.alt ?? '';
		altOpen = true;
	}
	async function saveAlt() {
		if (!altFor) return;
		const ok = await call('PATCH', `${base}/${altFor.id}`, { alt: altText.trim() });
		if (ok) altOpen = false;
	}

	const btn = 'btn btn-outline h-9 min-h-9 w-9 p-0';
</script>

<div class="grid gap-3">
	{#if errorMsg}<Alert kind="error" title={t('shop.image.failed')}>{errorMsg}</Alert>{/if}
	<input bind:this={input} type="file" accept="image/png,image/jpeg,image/webp" multiple class="hidden" onchange={upload} />
	{#if images.length}
		<ul class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3">
			{#each images as p, i (p.id)}
				<li class="grid gap-1.5">
					<div class="relative">
						<img src={p.url} alt={p.alt || name} class="aspect-square w-full rounded-lg border object-cover {p.is_main ? 'border-primary' : 'border-line'}" />
						{#if p.is_main}<span class="badge badge-primary badge-sm absolute top-1.5 left-1.5 gap-1"><Star size={11} aria-hidden="true" /> {t('shop.image.main')}</span>{/if}
					</div>
					<div class="flex flex-wrap gap-1">
						<button class={btn} type="button" disabled={busy || p.is_main} title={t('shop.image.set_main')} aria-label={t('shop.image.set_main_for', { n: i + 1 })} onclick={() => call('POST', `${base}/${p.id}/set-main`)}><Star size={14} aria-hidden="true" /></button>
						<button class={btn} type="button" disabled={busy || i === 0} title={t('shop.image.earlier')} aria-label={t('shop.image.earlier_for', { n: i + 1 })} onclick={() => move(i, -1)}><ArrowLeft size={14} aria-hidden="true" /></button>
						<button class={btn} type="button" disabled={busy || i === images.length - 1} title={t('shop.image.later')} aria-label={t('shop.image.later_for', { n: i + 1 })} onclick={() => move(i, 1)}><ArrowRight size={14} aria-hidden="true" /></button>
						<button class={btn} type="button" disabled={busy} title={t('shop.image.description')} aria-label={t('shop.image.description_for', { n: i + 1 })} onclick={() => openAlt(p)}><Pencil size={14} aria-hidden="true" /></button>
						{#if confirmId === p.id}
							<button class="btn btn-error h-9 min-h-9 gap-1 px-2 text-xs" type="button" disabled={busy} onblur={() => (confirmId = null)} onclick={() => remove(p)}><Trash2 size={14} aria-hidden="true" /><span class="ml-1">{t('shop.image.confirm_delete')}</span></button>
						{:else}
							<button class="{btn} btn-error" type="button" disabled={busy} title={t('shop.image.remove')} aria-label={t('shop.image.remove_for', { n: i + 1 })} onclick={() => remove(p)}><Trash2 size={14} aria-hidden="true" /></button>
						{/if}
					</div>
				</li>
			{/each}
		</ul>
	{:else}
		<div class="flex aspect-square w-full items-center justify-center rounded-lg border border-dashed border-line text-sm text-muted">{t('shop.image.none')}</div>
	{/if}
	<div class="flex flex-wrap items-center gap-2">
		<button class="btn btn-outline h-9 min-h-9 gap-2" type="button" disabled={busy || images.length >= MAX_COUNT} onclick={() => input?.click()}><ImagePlus size={14} aria-hidden="true" /> {busy ? t('shop.image.uploading') : t('shop.image.upload')}</button>
		<span class="text-xs text-muted">{images.length} / {MAX_COUNT}</span>
	</div>
	<p class="text-xs text-muted">{t('shop.image.help')}</p>
</div>

<Modal bind:open={altOpen} title={t('shop.image.description')}>
	<form class="grid gap-3" onsubmit={(e) => { e.preventDefault(); saveAlt(); }}>
		<label class="grid gap-1 text-sm">
			<span class="font-semibold">{t('shop.image.description_label')}</span>
			<input class="input h-9 w-full" type="text" maxlength="200" bind:value={altText} />
			<span class="text-xs text-muted">{t('shop.image.description_help')}</span>
		</label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline h-9 min-h-9" type="button" onclick={() => (altOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary h-9 min-h-9" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
