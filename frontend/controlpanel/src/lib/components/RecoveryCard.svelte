<script lang="ts">
	import { LifeBuoy } from '@lucide/svelte';
	import { auth, splitErrors, withReauth } from '#lib/api.ts';
	import type { Authenticator } from '#lib/types.ts';
	import Alert from './Alert.svelte';
	import CodesDialog from './CodesDialog.svelte';
	import Modal from './Modal.svelte';

	let { authenticator, canGenerate, onchange, openRequest = 0 }: {
		authenticator?: Extract<Authenticator, { type: 'recovery_codes' }>;
		/** Recovery codes only make sense once another factor exists. */
		canGenerate: boolean;
		onchange: () => void;
		/** Bumped by the parent to show the codes straight after enrolment. */
		openRequest?: number;
	} = $props();

	let codesOpen = $state(false);
	let codes = $state<string[]>([]);
	let confirmOpen = $state(false);
	let error = $state('');
	let busy = $state(false);

	async function view() {
		error = '';
		busy = true;
		const r = await withReauth(() => auth('GET', '/account/authenticators/recovery-codes'));
		busy = false;
		if (r.ok) {
			codes = r.data?.unused_codes ?? [];
			codesOpen = true;
		} else if (r.status !== 401) error = splitErrors(r).form[0] ?? 'Could not load your recovery codes.';
	}

	async function generate() {
		busy = true;
		error = '';
		const r = await withReauth(() => auth('POST', '/account/authenticators/recovery-codes'));
		busy = false;
		if (r.ok) {
			confirmOpen = false;
			onchange();
			await view();
		} else error = splitErrors(r).form[0] ?? 'Could not generate recovery codes.';
	}

	let lastRequest = 0;
	$effect(() => {
		if (openRequest > lastRequest) {
			lastRequest = openRequest;
			setTimeout(view, 300); // let the list reload so the card reflects the new codes
		}
	});
</script>

<div class="card">
	<div class="card-body">
		<div class="card-title">
			<LifeBuoy size={16} aria-hidden="true" /> Recovery codes
			{#if authenticator}<span class="badge badge-success ml-auto">{authenticator.unused_code_count} of {authenticator.total_code_count} left</span>{:else}<span class="badge badge-ghost ml-auto">none</span>{/if}
		</div>
		<p class="text-sm text-muted">One-time codes that let you sign in if you lose your authenticator or security key.</p>
		{#if authenticator && authenticator.unused_code_count <= 2}
			<Alert kind="warning">You're running low. Generate a new set soon.</Alert>
		{/if}
		{#if error && !codesOpen && !confirmOpen}<Alert kind="error">{error}</Alert>{/if}
		<div class="card-actions mt-2">
			{#if authenticator}
				<button class="btn btn-outline btn-sm" disabled={busy} onclick={view}>View codes</button>
				<button class="btn btn-outline btn-sm" disabled={busy} onclick={() => ((confirmOpen = true), (error = ''))}>Generate new set</button>
			{:else}
				<button class="btn btn-primary btn-sm" disabled={busy || !canGenerate} onclick={generate}>Generate codes</button>
				{#if !canGenerate}<span class="self-center text-xs text-muted">Set up another factor first.</span>{/if}
			{/if}
		</div>
	</div>
</div>

<CodesDialog bind:open={codesOpen} {codes} />

<Modal bind:open={confirmOpen} title="Generate a new set?">
	<p class="text-sm">Your current recovery codes will stop working immediately.</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (confirmOpen = false)}>Cancel</button>
		<button class="btn btn-primary" disabled={busy} onclick={generate}>Generate</button>
	</div>
</Modal>
