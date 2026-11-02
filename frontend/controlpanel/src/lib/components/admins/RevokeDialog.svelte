<script lang="ts">
	import { platform } from '#lib/api.ts';
	import type { PlatformAdmin } from '#lib/admins.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	let { admin = $bindable(null), ondone }: { admin: PlatformAdmin | null; ondone: (msg: string) => void } = $props();

	let open = $state(false);
	let error = $state('');
	let busy = $state(false);

	$effect(() => {
		open = !!admin;
		if (admin) error = '';
	});

	async function revoke() {
		if (!admin) return;
		busy = true;
		error = '';
		const r = await platform('DELETE', `/admins/${admin.id}`);
		busy = false;
		if (!r.ok) return (error = r.message ?? 'Access could not be revoked.');
		const who = admin.email;
		admin = null;
		ondone(`${who} no longer has platform access.`);
	}
</script>

<Modal bind:open title="Revoke platform access?" onclose={() => (admin = null)}>
	{#if admin}
		<p class="text-sm"><strong>{admin.email}</strong> will lose {admin.is_superuser ? 'superuser and staff' : 'staff'} access to the control panel immediately. Their account itself, and any club access, stays as it is. You can grant access again later.</p>
		{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
		<div class="modal-action">
			<button class="btn btn-outline" onclick={() => (admin = null)}>Keep access</button>
			<button class="btn btn-error" disabled={busy} onclick={revoke}>Revoke access</button>
		</div>
	{/if}
</Modal>
