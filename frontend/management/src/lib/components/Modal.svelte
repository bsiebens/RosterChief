<script lang="ts">
	import { X } from '@lucide/svelte';
	import type { Snippet } from 'svelte';
	import { t } from '#lib/i18n.svelte.ts';

	let {
		open = $bindable(false),
		title,
		onclose,
		wide = false,
		closable = false,
		quiet = true,
		aside,
		children
	}: { open: boolean; title: string; onclose?: () => void; wide?: boolean; closable?: boolean; /** Do not focus (and highlight) the first button or field when it opens; pass `false` to keep the browser's default. */ quiet?: boolean; aside?: Snippet; children: Snippet } = $props();

	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) {
			dialog.showModal();
		}
		if (!open && dialog.open) dialog.close();
	});
</script>

<dialog
	bind:this={dialog}
	class="modal m-auto bg-transparent p-0 outline-none backdrop:bg-ink/60 focus:outline-none focus-visible:outline-none"
	aria-label={title}
	onclose={() => {
		open = false;
		onclose?.();
	}}
>
	<div class="modal-box {wide ? 'w-[60rem]!' : ''}">
		<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
			<!-- The heading takes the initial focus (no ring) so the browser does not focus, and flash, the first button or field. -->
			<!-- svelte-ignore a11y_autofocus -->
			<h2 tabindex="-1" autofocus={quiet} class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase outline-none">{title}</h2>
			{#if (open && aside) || closable}
				<div class="flex items-center gap-2">
					{#if open && aside}{@render aside()}{/if}
					{#if closable}<button class="btn btn-ghost btn-square btn-sm" type="button" aria-label={t('common.close')} onclick={() => (open = false)}><X size={16} aria-hidden="true" /></button>{/if}
				</div>
			{/if}
		</div>
		{#if open}{@render children()}{/if}
	</div>
</dialog>
