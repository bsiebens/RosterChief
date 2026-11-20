<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { renderMarkdown } from '#lib/news.ts';

	/**
	 * The article as readers see it, Dutch and English. While the form has unsaved edits (`live`) the body is rendered
	 * by a small client-side approximation; otherwise by the server's own HTML (`html_nl` / `html_en`).
	 */
	let {
		title,
		body,
		title_en = '',
		body_en = '',
		html = '',
		live = true,
		photoUrl = null,
		focal = null,
		author = null
	}: {
		title: string;
		body: string;
		title_en?: string;
		body_en?: string;
		/** Server-rendered HTML of the Dutch body (saved version). */
		html?: string;
		live?: boolean;
		photoUrl?: string | null;
		focal?: { x: number; y: number } | null;
		author?: string | null;
	} = $props();

	let lang = $state<'nl' | 'en'>('nl');
	const hasEn = $derived(!!title_en.trim() || !!body_en.trim());
	const shownTitle = $derived((lang === 'en' && title_en.trim() ? title_en : title).trim());
	const shownBody = $derived(lang === 'en' && body_en.trim() ? body_en : body);
	// The server only renders the Dutch body; English always goes through the approximation.
	const rendered = $derived(!live && lang === 'nl' && html ? html : renderMarkdown(shownBody));
</script>

<article class="card overflow-hidden">
	{#if photoUrl}
		<img class="h-48 w-full object-cover" style:object-position={focal ? `${focal.x}% ${focal.y}%` : undefined} src={photoUrl} alt="" />
	{/if}
	<div class="card-body">
		<div class="mb-1 flex flex-wrap items-center justify-end gap-3">
			{#if lang === 'en' && !hasEn}<span class="mr-auto font-mono text-xs text-dim">{t('news.preview.no_en')}</span>{/if}
			<div class="flex w-fit items-center gap-1 " role="group" aria-label={t('news.card.preview')}>
				{#each ['nl', 'en'] as const as l (l)}
					<button type="button" aria-pressed={lang === l} class="rounded-md px-3 py-1 font-display text-xs font-bold tracking-[.08em] uppercase {lang === l ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}" onclick={() => (lang = l)}>{t(`news.preview.${l}`)}</button>
				{/each}
			</div>
		</div>

		<h2 class="font-display text-3xl leading-[1.05] font-extrabold break-words text-ink uppercase">{shownTitle || t('news.preview.untitled')}</h2>
		<div class="my-2 h-[3px] w-14 bg-club"></div>
		{#if rendered}
			<!-- Server HTML is sanitised by the backend; the live approximation escapes its input first. -->
			<div class="news-body text-slate">{@html rendered}</div>
		{:else}
			<p class="text-sm text-muted">{t('news.preview.empty')}</p>
		{/if}
		{#if author}<div class="mt-2 border-t border-line pt-3 text-sm text-muted">{t('news.preview.posted_by', { name: author })}</div>{/if}
		{#if live}<p class="font-mono text-[11px] text-dim">{t('news.preview.draft_note')}</p>{/if}
	</div>
</article>
