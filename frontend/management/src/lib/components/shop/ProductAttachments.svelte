<script lang="ts">
	import { Check, Download, File as FileIcon, FileSpreadsheet, FileText, Image as ImageIcon, Paperclip, Pencil, Trash2, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { ProductAttachmentItem, ProductDetail } from '#lib/shop.ts';

	/** Product files (size guide, care sheet...): upload, rename and delete, straight from the browser. */
	let { productId, attachments = $bindable() }: { productId: string; attachments: ProductAttachmentItem[] } = $props();

	const MAX_COUNT = 10;
	const MAX_BYTES = 10 * 1024 * 1024;
	const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.gif,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp,.txt,.csv';
	let busy = $state(false);
	let errorMsg = $state('');
	let input: HTMLInputElement | undefined = $state();
	let picked = $state<File[]>([]);
	let newTitle = $state('');
	let confirmId = $state<string | null>(null);
	const base = $derived(`/shop/products/${productId}/attachments`);
	const fail = (r: { message?: string; fields: Record<string, string> }) => (errorMsg = r.fields.files || r.fields.title || r.fields.__all__ || r.message || t('common.error.generic'));

	function size(n: number) {
		if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
		return `${Math.max(1, Math.round(n / 1024))} KB`;
	}
	function iconOf(a: ProductAttachmentItem) {
		const c = a.content_type;
		if (c.startsWith('image/')) return ImageIcon;
		if (c.includes('sheet') || c.includes('excel') || c.includes('csv')) return FileSpreadsheet;
		if (c === 'application/pdf' || c.startsWith('text/') || c.includes('word') || c.includes('opendocument')) return FileText;
		return FileIcon;
	}

	function pick() {
		errorMsg = '';
		picked = input?.files ? [...input.files] : [];
		newTitle = '';
	}
	function cancelPick() {
		picked = [];
		if (input) input.value = '';
	}
	async function upload() {
		if (!picked.length) return;
		if (attachments.length + picked.length > MAX_COUNT) return void (errorMsg = t('shop.attach.err.count', { max: MAX_COUNT }));
		const big = picked.find((f) => f.size > MAX_BYTES);
		if (big) return void (errorMsg = t('shop.attach.err.size', { name: big.name }));
		busy = true;
		errorMsg = '';
		const form = new FormData();
		for (const f of picked) form.append('files', f);
		if (picked.length === 1 && newTitle.trim()) form.append('title', newTitle.trim());
		const res = await api<ProductDetail>('POST', base, { form });
		busy = false;
		if (res.ok && res.data) {
			attachments = res.data.attachments ?? [];
			cancelPick();
		} else fail(res);
	}

	let editId = $state<string | null>(null);
	let editTitle = $state('');
	function startEdit(a: ProductAttachmentItem) {
		editId = a.id;
		editTitle = a.title;
		confirmId = null;
	}
	async function saveTitle() {
		const title = editTitle.trim();
		if (!editId || !title) return;
		busy = true;
		errorMsg = '';
		const res = await api<ProductDetail>('PATCH', `${base}/${editId}`, { body: { title } });
		busy = false;
		if (res.ok && res.data) {
			attachments = res.data.attachments ?? [];
			editId = null;
		} else fail(res);
	}
	async function remove(a: ProductAttachmentItem) {
		if (confirmId !== a.id) {
			confirmId = a.id;
			return;
		}
		busy = true;
		errorMsg = '';
		confirmId = null;
		const res = await api<ProductDetail>('DELETE', `${base}/${a.id}`);
		busy = false;
		if (res.ok && res.data) attachments = res.data.attachments ?? [];
		else fail(res);
	}

	const btn = 'btn btn-outline h-9 min-h-9 w-9 p-0';
</script>

<div class="grid gap-3">
	{#if errorMsg}<Alert kind="error" title={t('shop.attach.failed')}>{errorMsg}</Alert>{/if}
	{#if attachments.length}
		<ul class="grid gap-2">
			{#each attachments as a (a.id)}
				{@const Icon = iconOf(a)}
				<li class="flex flex-wrap items-center gap-2 rounded-lg border border-line p-2">
					<Icon size={20} class="shrink-0 text-muted" aria-hidden="true" />
					{#if editId === a.id}
						<form class="flex min-w-0 flex-1 items-center gap-1" onsubmit={(e) => { e.preventDefault(); saveTitle(); }}>
							<input class="input h-9 min-w-0 flex-1" type="text" maxlength="200" aria-label={t('shop.attach.title')} bind:value={editTitle} />
							<button class={btn} type="submit" disabled={busy || !editTitle.trim()} title={t('common.save')} aria-label={t('common.save')}><Check size={14} aria-hidden="true" /></button>
							<button class={btn} type="button" title={t('common.cancel')} aria-label={t('common.cancel')} onclick={() => (editId = null)}><X size={14} aria-hidden="true" /></button>
						</form>
					{:else}
						<div class="grid min-w-0 flex-1">
							<span class="truncate text-sm font-semibold">{a.title}</span>
							<span class="truncate text-xs text-muted">{a.name} · {size(a.size)}</span>
						</div>
						<div class="flex gap-1">
							<button class={btn} type="button" disabled={busy} title={t('shop.attach.rename')} aria-label={t('shop.attach.rename_for', { name: a.title })} onclick={() => startEdit(a)}><Pencil size={14} aria-hidden="true" /></button>
							<a class={btn} href={a.url} target="_blank" rel="noopener" title={t('shop.attach.open')} aria-label={t('shop.attach.open_for', { name: a.title })}><Download size={14} aria-hidden="true" /></a>
							{#if confirmId === a.id}
								<button class="btn btn-error h-9 min-h-9 gap-1 px-2 text-xs" type="button" disabled={busy} onblur={() => (confirmId = null)} onclick={() => remove(a)}><Trash2 size={14} aria-hidden="true" /><span class="ml-1">{t('shop.attach.confirm_delete')}</span></button>
							{:else}
								<button class="{btn} btn-error" type="button" disabled={busy} title={t('shop.attach.remove')} aria-label={t('shop.attach.remove_for', { name: a.title })} onclick={() => remove(a)}><Trash2 size={14} aria-hidden="true" /></button>
							{/if}
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<p class="rounded-lg border border-dashed border-line p-4 text-center text-sm text-muted">{t('shop.attach.none')}</p>
	{/if}

	<input bind:this={input} type="file" accept={ACCEPT} multiple class="hidden" onchange={pick} />
	{#if picked.length}
		<div class="grid gap-2 rounded-lg border border-line p-2">
			<p class="text-xs text-muted">{picked.length === 1 ? picked[0].name : t('shop.attach.picked', { n: picked.length })}</p>
			{#if picked.length === 1}
				<input class="input h-9 w-full" type="text" maxlength="200" placeholder={t('shop.attach.title_optional')} aria-label={t('shop.attach.title')} bind:value={newTitle} />
			{/if}
			<div class="flex gap-2">
				<button class="btn btn-primary h-9 min-h-9 gap-2" type="button" disabled={busy} onclick={upload}><Paperclip size={14} aria-hidden="true" /> {busy ? t('shop.attach.uploading') : t('shop.attach.upload_now')}</button>
				<button class="btn btn-outline h-9 min-h-9" type="button" disabled={busy} onclick={cancelPick}>{t('common.cancel')}</button>
			</div>
		</div>
	{:else}
		<div class="flex flex-wrap items-center gap-2">
			<button class="btn btn-outline h-9 min-h-9 gap-2" type="button" disabled={busy || attachments.length >= MAX_COUNT} onclick={() => input?.click()}><Paperclip size={14} aria-hidden="true" /> {t('shop.attach.add')}</button>
			<span class="text-xs text-muted">{attachments.length} / {MAX_COUNT}</span>
		</div>
	{/if}
	<p class="text-xs text-muted">{t('shop.attach.help')}</p>
</div>
