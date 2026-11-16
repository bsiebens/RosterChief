<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	/** Prev/next pager for limit/offset lists. `href(offset)` builds the link so filters in the URL are preserved. */
	let { count, offset, limit, href }: { count: number; offset: number; limit: number; href: (offset: number) => string } = $props();
	const page = $derived(Math.floor(offset / limit) + 1);
	const pages = $derived(Math.max(1, Math.ceil(count / limit)));
</script>

{#if pages > 1}
	<nav class="flex items-center justify-center gap-3 py-3" aria-label="Pagination">
		<a class="btn btn-sm btn-outline {page <= 1 ? 'pointer-events-none opacity-45' : ''}" href={href(Math.max(0, offset - limit))} aria-disabled={page <= 1}>{t('common.previous')}</a>
		<span class="font-mono text-xs text-muted">{page} / {pages}</span>
		<a class="btn btn-sm btn-outline {page >= pages ? 'pointer-events-none opacity-45' : ''}" href={href(offset + limit)} aria-disabled={page >= pages}>{t('common.next')}</a>
	</nav>
{/if}
