<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		open = $bindable(false),
		title,
		onclose,
		wide = false,
		aside,
		children
	}: { open: boolean; title: string; onclose?: () => void; wide?: boolean; aside?: Snippet; children: Snippet } = $props();

	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});
</script>

<dialog
	bind:this={dialog}
	class="modal m-auto bg-transparent p-0 backdrop:bg-ink/60"
	aria-label={title}
	onclose={() => {
		open = false;
		onclose?.();
	}}
>
	<div class="modal-box {wide ? 'w-[60rem]!' : ''}">
		<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
			<h2 class="font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{title}</h2>
			{#if open && aside}{@render aside()}{/if}
		</div>
		{#if open}{@render children()}{/if}
	</div>
</dialog>
