<script lang="ts">
	import { Copy, Download } from '@lucide/svelte';
	import Modal from './Modal.svelte';

	let { open = $bindable(false), codes, heading = 'Recovery codes' }: { open: boolean; codes: string[]; heading?: string } = $props();

	let copied = $state(false);

	async function copy() {
		try {
			await navigator.clipboard.writeText(codes.join('\n'));
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			// clipboard unavailable: the codes are still on screen
		}
	}

	function download() {
		const url = URL.createObjectURL(new Blob([codes.join('\n') + '\n'], { type: 'text/plain' }));
		const a = Object.assign(document.createElement('a'), { href: url, download: 'rosterchief-recovery-codes.txt' });
		a.click();
		URL.revokeObjectURL(url);
	}
</script>

<Modal bind:open title={heading}>
	<p class="mb-3 text-sm text-muted">Each code works once if you lose access to your authenticator. Store them somewhere safe, like a password manager.</p>
	{#if codes.length}
		<ul class="grid grid-cols-2 gap-x-6 gap-y-1 rounded border border-edge bg-subhead p-4 font-mono text-sm text-ink">
			{#each codes as code (code)}<li>{code}</li>{/each}
		</ul>
	{:else}
		<p class="text-sm">These codes can't be shown again. Generate a new set to see them.</p>
	{/if}
	<div class="modal-action">
		{#if codes.length}
			<button class="btn btn-outline btn-sm" onclick={copy}><Copy size={14} aria-hidden="true" /> {copied ? 'Copied' : 'Copy'}</button>
			<button class="btn btn-outline btn-sm" onclick={download}><Download size={14} aria-hidden="true" /> Download</button>
		{/if}
		<button class="btn btn-primary btn-sm" onclick={() => (open = false)}>Done</button>
	</div>
</Modal>
