<script lang="ts">
	import { platform } from '#lib/api.ts';
	import Alert from '../Alert.svelte';
	import Field from '../Field.svelte';
	import Modal from '../Modal.svelte';

	let { open = $bindable(false), ondone }: { open: boolean; ondone: (msg: string) => void } = $props();

	let email = $state('');
	let first = $state('');
	let last = $state('');
	let superuser = $state(false);
	let fields = $state<Record<string, string>>({});
	let error = $state('');
	let busy = $state(false);

	function reset() {
		email = first = last = error = '';
		superuser = false;
		fields = {};
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const address = email.trim();
		const r = await platform('POST', '/admins', { email: address, first_name: first.trim(), last_name: last.trim(), is_superuser: superuser }, fetch, { 'Idempotency-Key': crypto.randomUUID() });
		busy = false;
		if (!r.ok) {
			fields = r.fields;
			error =
				r.status === 409 && !r.fields.email
					? `${r.message} Ask the person to verify that email address on their account first.`
					: (r.message ?? '');
			return;
		}
		open = false;
		reset();
		ondone(`${address} now has ${superuser ? 'superuser' : 'staff'} access.`);
	}
</script>

<Modal bind:open title="Grant platform access" onclose={reset}>
	<form class="flex flex-col gap-4" onsubmit={submit}>
		<p class="text-sm text-muted">An existing account (with a verified email) is promoted. An address without an account gets a new one and a password setup email, and then a first and last name are required.</p>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label="Email" type="email" required autocomplete="off" bind:value={email} error={fields.email} />
		<div class="grid grid-cols-2 gap-3">
			<Field label="First name" autocomplete="off" bind:value={first} error={fields.first_name} />
			<Field label="Last name" autocomplete="off" bind:value={last} error={fields.last_name} />
		</div>
		<fieldset class="flex flex-col gap-2">
			<legend class="label-text mb-1">Level</legend>
			<label class="flex items-start gap-2 text-sm"><input type="radio" class="mt-1" name="level" checked={!superuser} onchange={() => (superuser = false)} /><span><strong>Staff</strong> can see all clubs and support them.</span></label>
			<label class="flex items-start gap-2 text-sm"><input type="radio" class="mt-1" name="level" checked={superuser} onchange={() => (superuser = true)} /><span><strong>Superuser</strong> can additionally manage platform admins, jobs and switches.</span></label>
		</fieldset>
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (open = false)}>Cancel</button>
			<button type="submit" class="btn btn-primary" disabled={busy || !email.trim()}>Grant access</button>
		</div>
	</form>
</Modal>
