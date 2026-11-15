<script lang="ts">
	import { Crosshair, Image as ImageIcon, ImagePlus, Star, Trash2, Camera } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { NewsDetail, NewsPhoto } from '#lib/news.ts';

	/**
	 * Photo manager: upload (several at once), main photo, focal point picker, delete. Every action calls the API
	 * straight from the browser and updates `photos`; the page does not reload.
	 */
	let { newsId, photos = $bindable(), canEdit }: { newsId: string; photos: NewsPhoto[]; canEdit: boolean } = $props();

	let busy = $state(false);
	let errorMsg = $state('');
	let input: HTMLInputElement | undefined = $state();
	const fail = (r: { message?: string; fields: Record<string, string> }) => (errorMsg = r.fields.image || r.fields.__all__ || r.message || t('common.error.generic'));

	async function upload() {
		const files = input?.files ? [...input.files] : [];
		if (!files.length) return;
		busy = true;
		errorMsg = '';
		for (const file of files) {
			const form = new FormData();
			form.append('image', file);
			const res = await api<NewsPhoto>('POST', `/news/${newsId}/photos`, { form });
			if (res.ok && res.data) photos = [...photos, res.data];
			else {
				fail(res);
				break;
			}
		}
		busy = false;
		if (input) input.value = '';
	}

	async function setMain(p: NewsPhoto) {
		busy = true;
		errorMsg = '';
		const res = await api<NewsDetail>('POST', `/news/${newsId}/photos/${p.id}/set-main`);
		busy = false;
		if (res.ok && res.data) photos = res.data.photos;
		else fail(res);
	}

	// -- delete --
	let deleteOpen = $state(false);
	let deleting = $state<NewsPhoto | null>(null);
	async function confirmDelete() {
		if (!deleting) return;
		const id = deleting.id;
		busy = true;
		errorMsg = '';
		const res = await api('DELETE', `/news/${newsId}/photos/${id}`);
		busy = false;
		deleteOpen = false;
		if (!res.ok) return fail(res);
		const wasMain = deleting.is_main;
		photos = photos.filter((p) => p.id !== id);
		// The API promotes another photo when the main one goes; mirror it until the next load.
		if (wasMain && photos.length && !photos.some((p) => p.is_main)) photos = photos.map((p, i) => (i === 0 ? { ...p, is_main: true } : p));
	}

	// -- focal point --
	let focalOpen = $state(false);
	let focalFor = $state<NewsPhoto | null>(null);
	let fx = $state(50);
	let fy = $state(50);
	function openFocal(p: NewsPhoto) {
		focalFor = p;
		fx = p.focal_x;
		fy = p.focal_y;
		focalOpen = true;
	}
	function pick(e: PointerEvent) {
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		fx = Math.max(0, Math.min(100, Math.round(((e.clientX - r.left) / r.width) * 100)));
		fy = Math.max(0, Math.min(100, Math.round(((e.clientY - r.top) / r.height) * 100)));
	}
	function onKey(e: KeyboardEvent) {
		const step = e.shiftKey ? 10 : 1;
		const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
		if (!d) return;
		e.preventDefault();
		fx = Math.max(0, Math.min(100, fx + d[0]));
		fy = Math.max(0, Math.min(100, fy + d[1]));
	}
	let dragging = false;
	async function saveFocal() {
		if (!focalFor) return;
		const id = focalFor.id;
		busy = true;
		errorMsg = '';
		const res = await api<NewsPhoto>('PATCH', `/news/${newsId}/photos/${id}`, { body: { focal_x: fx, focal_y: fy } });
		busy = false;
		focalOpen = false;
		if (res.ok) photos = photos.map((p) => (p.id === id ? { ...p, focal_x: fx, focal_y: fy } : p));
		else fail(res);
	}
</script>

<section class="card card-body" aria-labelledby="news-photos-h">
	<header class="mb-3 flex flex-wrap items-start justify-between gap-3">
		<div class="grid gap-0.5">
			<h2 id="news-photos-h" class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Camera size={16} aria-hidden="true" /> {t('news.card.photos')} <span class="font-mono text-xs font-normal text-muted">· {photos.length}</span></h2>
			<p class="text-xs text-muted">{t('news.card.photos_desc')}</p>
		</div>
		{#if canEdit}
			<label class="btn btn-outline btn-sm cursor-pointer gap-2 {busy ? 'pointer-events-none opacity-60' : ''}">
				<ImagePlus size={14} aria-hidden="true" />
				{busy ? t('news.photos.uploading') : t('news.photos.add')}
				<input bind:this={input} type="file" accept="image/*" multiple class="sr-only" onchange={upload} disabled={busy} />
			</label>
		{/if}
	</header>

	{#if errorMsg}<div class="mb-3"><Alert kind="error" title={t('news.photos.failed')}>{errorMsg}</Alert></div>{/if}

	{#if photos.length}
		<ul class="grid grid-cols-2 gap-4 md:grid-cols-4">
			{#each photos as p, i (p.id)}
				<li class="relative">
					<img class="aspect-square w-full rounded-lg border border-line object-cover" style:object-position="{p.focal_x}% {p.focal_y}%" src={p.url} alt={t('news.photos.alt', { n: i + 1 })} />
					{#if p.is_main}<span class="badge badge-success badge-sm absolute top-1.5 left-1.5">{t('news.photos.main')}</span>{/if}
					{#if canEdit}
						<div class="mt-1.5 flex flex-wrap gap-1">
							{#if !p.is_main}<button class="btn btn-outline btn-xs gap-1" type="button" disabled={busy} onclick={() => setMain(p)}><Star size={12} aria-hidden="true" /> {t('news.photos.set_main')}</button>{/if}
							<button class="btn btn-outline btn-xs gap-1" type="button" disabled={busy} onclick={() => openFocal(p)}><Crosshair size={12} aria-hidden="true" /> {t('news.photos.focal')}</button>
							<button class="btn btn-outline btn-error btn-xs gap-1" type="button" disabled={busy} onclick={() => ((deleting = p), (deleteOpen = true))}><Trash2 size={12} aria-hidden="true" /> {t('news.photos.delete')}</button>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<p class="text-sm text-muted">{t('news.photos.none')}</p>
	{/if}
</section>

<Modal bind:open={focalOpen} title={t('news.focal.title')}>
	{#if focalFor}
		<div class="grid gap-3">
			<p class="text-sm text-muted">{t('news.focal.help')}</p>
			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<div
				class="relative cursor-crosshair overflow-hidden rounded-lg border border-line select-none focus-visible:ring-2 focus-visible:ring-club"
				role="slider"
				tabindex="0"
				aria-label={t('news.focal.point', { x: fx, y: fy })}
				aria-valuenow={fx}
				aria-valuemin="0"
				aria-valuemax="100"
				onpointerdown={(e) => ((dragging = true), (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId), pick(e))}
				onpointermove={(e) => dragging && pick(e)}
				onpointerup={() => (dragging = false)}
				onkeydown={onKey}>
				<img class="block max-h-[60vh] w-full object-contain" src={focalFor.url} alt="" draggable="false" />
				<div class="pointer-events-none absolute size-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-club/80 shadow-md" style:left="{fx}%" style:top="{fy}%"></div>
			</div>
			<div class="font-mono text-xs text-muted" aria-live="polite">{t('news.focal.point', { x: fx, y: fy })}</div>
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (focalOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary gap-2" type="button" disabled={busy} onclick={saveFocal}><Crosshair size={16} /> {t('news.focal.save')}</button>
			</div>
		</div>
	{/if}
</Modal>

<Modal bind:open={deleteOpen} title={t('news.photos.delete_title')}>
	<div class="grid gap-3">
		<p>{t('news.photos.delete_body')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={busy} onclick={confirmDelete}><Trash2 size={16} /> {t('news.photos.delete')}</button>
		</div>
	</div>
</Modal>
