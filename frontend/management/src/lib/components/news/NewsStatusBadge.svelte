<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { statusKey, type NewsListItem } from '#lib/news.ts';

	let { item, small = false }: { item: Pick<NewsListItem, 'status' | 'is_scheduled'>; small?: boolean } = $props();
	const key = $derived(statusKey(item));
	const tone = $derived({ draft: '', pending_review: 'badge-warning', scheduled: 'badge-info', published: 'badge-success' }[key]);
</script>

<span class="badge {tone} {small ? 'badge-sm' : ''}">{t(`news.status.${key}`)}</span>
