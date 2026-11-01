<script lang="ts">
	import { Pencil, Trash2, Usb } from '@lucide/svelte';
	import { auth, createCredential, splitErrors, webauthnMessage, webauthnSupported, withReauth } from '#lib/api.ts';
	import type { Authenticator } from '#lib/types.ts';
	import Alert from './Alert.svelte';
	import Field from './Field.svelte';
	import Modal from './Modal.svelte';

	type Key = Extract<Authenticator, { type: 'webauthn' }>;
	let { keys, onchange, onnewcodes }: { keys: Key[]; onchange: () => void; onnewcodes: () => void } = $props();

	const supported = $derived(webauthnSupported());

	let addOpen = $state(false);
	let name = $state('');
	let passwordless = $state(false);
	let renaming = $state<Key | null>(null);
	let removing = $state<Key | null>(null);
	let error = $state('');
	let busy = $state(false);

	const fmt = (t: number) => new Date(t * 1000).toLocaleDateString();

	async function add(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		try {
			const begin = await withReauth(() => auth('GET', `/account/authenticators/webauthn${passwordless ? '?passwordless' : ''}`));
			if (!begin.ok) return (error = splitErrors(begin).form[0] ?? 'Could not start registration.');
			const credential = await createCredential(begin.data.creation_options);
			if (!credential) return;
			const r = await withReauth(() => auth('POST', '/account/authenticators/webauthn', { name: name.trim(), credential }));
			if (!r.ok) {
				const e = splitErrors(r);
				return (error = e.fields.name ?? e.form[0] ?? 'The security key could not be added.');
			}
			addOpen = false;
			name = '';
			onchange();
			if (r.meta?.recovery_codes_generated) onnewcodes();
		} catch (e) {
			error = webauthnMessage(e);
		} finally {
			busy = false;
		}
	}

	async function rename(event: SubmitEvent) {
		event.preventDefault();
		if (!renaming) return;
		busy = true;
		error = '';
		const r = await withReauth(() => auth('PUT', '/account/authenticators/webauthn', { id: renaming!.id, name: name.trim() }));
		busy = false;
		if (r.ok) {
			renaming = null;
			onchange();
		} else error = splitErrors(r).fields.name ?? splitErrors(r).form[0] ?? 'Could not rename the key.';
	}

	async function remove() {
		if (!removing) return;
		busy = true;
		error = '';
		const r = await withReauth(() => auth('DELETE', '/account/authenticators/webauthn', { authenticators: [removing!.id] }));
		busy = false;
		if (r.ok) {
			removing = null;
			onchange();
		} else error = splitErrors(r).form[0] ?? 'Could not remove the key.';
	}
</script>

<div class="card">
	<div class="card-body">
		<div class="card-title">
			<Usb size={16} aria-hidden="true" /> Security keys &amp; passkeys
			{#if keys.length}<span class="badge badge-success ml-auto">{keys.length} registered</span>{:else}<span class="badge badge-ghost ml-auto">none</span>{/if}
		</div>
		<p class="text-sm text-muted">Hardware keys, Touch ID, Windows Hello or a phone. Passkeys can also sign you in without a password.</p>

		{#if keys.length}
			<ul class="divide-y">
				{#each keys as key (key.id)}
					<li class="flex items-center gap-3 py-2.5">
						<div class="min-w-0 flex-1">
							<div class="truncate text-sm font-semibold text-ink">{key.name}{#if key.is_passwordless} <span class="badge badge-info badge-sm ml-1">passkey</span>{/if}</div>
							<div class="font-mono text-[11px] text-muted">Added {fmt(key.created_at)}{key.last_used_at ? ` · last used ${fmt(key.last_used_at)}` : ' · never used'}</div>
						</div>
						<button class="btn btn-ghost btn-square btn-sm" aria-label="Rename {key.name}" onclick={() => ((renaming = key), (name = key.name), (error = ''))}><Pencil size={14} aria-hidden="true" /></button>
						<button class="btn btn-ghost btn-square btn-sm" aria-label="Remove {key.name}" onclick={() => ((removing = key), (error = ''))}><Trash2 size={14} aria-hidden="true" /></button>
					</li>
				{/each}
			</ul>
		{/if}

		{#if !supported}
			<Alert kind="info">This browser can't register security keys. Use a current version of Chrome, Safari, Edge or Firefox.</Alert>
		{/if}
		<div class="card-actions mt-2">
			<button class="btn btn-primary btn-sm" disabled={!supported} onclick={() => ((addOpen = true), (name = ''), (passwordless = false), (error = ''))}>Add a key</button>
		</div>
	</div>
</div>

<Modal bind:open={addOpen} title="Add a security key">
	<form class="flex flex-col gap-4" onsubmit={add}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label="Name" placeholder="e.g. YubiKey, MacBook Touch ID" maxlength={100} required bind:value={name} help="So you can tell your keys apart." />
		<label class="flex cursor-pointer items-start gap-3 text-sm">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={passwordless} />
			<span><span class="font-semibold text-ink">Create a passkey</span><br /><span class="text-muted">Lets you sign in without a password. Needs a key or device that stores passkeys.</span></span>
		</label>
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (addOpen = false)}>Cancel</button>
			<button type="submit" class="btn btn-primary" disabled={busy || !name.trim()}>Continue</button>
		</div>
	</form>
</Modal>

<Modal open={!!renaming} title="Rename key" onclose={() => (renaming = null)}>
	<form class="flex flex-col gap-4" onsubmit={rename}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label="Name" maxlength={100} required bind:value={name} />
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (renaming = null)}>Cancel</button>
			<button type="submit" class="btn btn-primary" disabled={busy || !name.trim()}>Save</button>
		</div>
	</form>
</Modal>

<Modal open={!!removing} title="Remove this key?" onclose={() => (removing = null)}>
	<p class="text-sm"><strong>{removing?.name}</strong> will no longer work for signing in.</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (removing = null)}>Keep it</button>
		<button class="btn btn-error" disabled={busy} onclick={remove}>Remove</button>
	</div>
</Modal>
