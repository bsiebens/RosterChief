<script lang="ts">
	import { platform } from '#lib/api.ts';
	import { adminName, type PlatformAdmin } from '#lib/admins.ts';
	import Alert from '../Alert.svelte';
	import Modal from '../Modal.svelte';

	let { admin = $bindable(null), ondone }: { admin: PlatformAdmin | null; ondone: (msg: string) => void } = $props();

	let open = $state(false);
	let superuser = $state(false);
	let error = $state('');
	let busy = $state(false);

	$effect(() => {
		open = !!admin;
		if (admin) {
			superuser = admin.is_superuser;
			error = '';
		}
	});

	async function save() {
		if (!admin) return;
		busy = true;
		error = '';
		const r = await platform('PATCH', `/admins/${admin.id}`, { is_staff: true, is_superuser: superuser });
		busy = false;
		if (!r.ok) return (error = r.message ?? 'The level could not be changed.');
		const who = admin.email;
		admin = null;
		ondone(`${who} is now ${superuser ? 'a superuser' : 'staff'}.`);
	}
</script>

<Modal bind:open title="Change level" onclose={() => (admin = null)}>
	{#if admin}
		<p class="text-sm"><strong>{adminName(admin)}</strong> <span class="font-mono text-xs text-muted">{admin.email}</span></p>
		<fieldset class="mt-3 flex flex-col gap-2">
			<legend class="label-text mb-1">Level</legend>
			<label class="flex items-start gap-2 text-sm"><input type="radio" class="mt-1" name="lvl" checked={!superuser} onchange={() => (superuser = false)} /><span><strong>Staff</strong> loses the ability to manage admins, jobs and switches.</span></label>
			<label class="flex items-start gap-2 text-sm"><input type="radio" class="mt-1" name="lvl" checked={superuser} onchange={() => (superuser = true)} /><span><strong>Superuser</strong> gets full control of the platform.</span></label>
		</fieldset>
		{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
		<div class="modal-action">
			<button class="btn btn-outline" onclick={() => (admin = null)}>Cancel</button>
			<button class="btn btn-primary" disabled={busy || superuser === admin.is_superuser} onclick={save}>Save level</button>
		</div>
	{/if}
</Modal>
