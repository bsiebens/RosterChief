<script lang="ts">
	import { FileText, X } from '@lucide/svelte';
	import { fileSize, isImage, type Attachment } from '#lib/chat.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * An attachment as a chip. In a message (no `onremove`) it is a link that opens the file; in the composer it is a preview with a
	 * remove button. Images show a thumbnail, PDFs a file icon.
	 */
	let { attachment, onremove }: { attachment: Attachment; onremove?: () => void } = $props();
</script>

<span class="inline-flex max-w-full min-w-0 items-center gap-2 rounded-lg border border-line bg-white py-1 pr-2 pl-1 text-xs text-ink">
	<a class="inline-flex min-w-0 items-center gap-2 no-underline" href={attachment.url} target="_blank" rel="noopener">
		{#if isImage(attachment)}
			<img class="size-8 shrink-0 rounded object-cover" src={attachment.url} alt="" />
		{:else}
			<span class="grid size-8 shrink-0 place-items-center rounded bg-paper text-muted"><FileText size={16} aria-hidden="true" /></span>
		{/if}
		<span class="min-w-0"><span class="block truncate font-semibold">{attachment.name}</span><span class="block text-[11px] text-muted">{fileSize(attachment.size)}</span></span>
	</a>
	{#if onremove}
		<button type="button" class="grid size-6 shrink-0 place-items-center rounded-full text-muted hover:bg-paper" aria-label={t('chat.composer.remove', { name: attachment.name })} onclick={onremove}><X size={14} aria-hidden="true" /></button>
	{/if}
</span>
