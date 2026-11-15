<script lang="ts">
	import { X } from '@lucide/svelte';
	import type { Snippet } from 'svelte';
	import { t } from '#lib/i18n.svelte.ts';

	let { title, open, onclose, children }: { title: string; open: boolean; onclose: () => void; children: Snippet } = $props();
	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});
</script>

<dialog bind:this={dialog} class="portal-sheet" aria-label={title} onclose={() => { if (open) onclose(); }}
	onclick={(event) => {
		// The backdrop is part of the dialog: a click outside its box is a click on the dimmed page.
		const box = dialog?.getBoundingClientRect();
		if (box && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) dialog?.close();
	}}>
	{#if open}
		<div class="sticky top-0 z-10 flex justify-end bg-(--portal-panel) px-3 py-2">
			<button type="button" class="btn btn-outline btn-square btn-sm" aria-label={t('common.close')} onclick={() => dialog?.close()}>
				<X size={16} aria-hidden="true" />
			</button>
		</div>
		{@render children()}
	{/if}
</dialog>
