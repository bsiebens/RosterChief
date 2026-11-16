<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		open = $bindable(false),
		title,
		onclose,
		wide = false,
		children
	}: { open: boolean; title: string; onclose?: () => void; wide?: boolean; children: Snippet } = $props();

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
		<h2 class="mb-4 font-display text-lg font-extrabold tracking-[.06em] text-ink uppercase">{title}</h2>
		{#if open}{@render children()}{/if}
	</div>
</dialog>
