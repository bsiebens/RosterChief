<script lang="ts">
	import type { Snippet } from 'svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';

	// Mounted by its parent only while it should be visible; `onclose` unmounts it.
	let {
		title,
		confirmLabel,
		danger = false,
		onconfirm,
		onclose,
		children
	}: { title: string; confirmLabel: string; danger?: boolean; onconfirm: () => Promise<string | null | void>; onclose: () => void; children: Snippet } = $props();

	let open = $state(true);
	let busy = $state(false);
	let error = $state('');

	async function confirm() {
		busy = true;
		error = '';
		const message = await onconfirm();
		busy = false;
		if (message) error = message;
		else open = false;
	}
</script>

<Modal bind:open {title} {onclose}>
	<div class="flex flex-col gap-4">
		<div class="text-sm">{@render children()}</div>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<div class="flex justify-end gap-2">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)} disabled={busy}>Cancel</button>
			<button type="button" class="btn {danger ? 'btn-error' : 'btn-primary'}" onclick={confirm} disabled={busy}>{busy ? 'Working…' : confirmLabel}</button>
		</div>
	</div>
</Modal>
