<script lang="ts">
	import { auth, splitErrors, withReauth } from '#lib/api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';

	let current = $state('');
	let next = $state('');
	let confirm = $state('');
	let busy = $state(false);
	let done = $state(false);
	let errors = $state<string[]>([]);
	let fieldErrors = $state<Record<string, string>>({});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		errors = [];
		fieldErrors = {};
		done = false;
		if (next !== confirm) {
			fieldErrors = { confirm: 'The two passwords do not match.' };
			return;
		}
		busy = true;
		const r = await withReauth(() => auth('POST', '/account/password/change', { current_password: current, new_password: next }));
		if (r.ok) {
			done = true;
			current = next = confirm = '';
		} else {
			const e = splitErrors(r);
			errors = e.form;
			fieldErrors = e.fields;
		}
		busy = false;
	}
</script>

<svelte:head><title>Change password · RosterChief</title></svelte:head>

<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Change password</h1>

<div class="card">
	<form class="card-body gap-4" onsubmit={submit}>
		{#if done}<Alert kind="success">Your password has been changed.</Alert>{/if}
		{#each errors as message (message)}<Alert kind="error">{message}</Alert>{/each}
		<Field label="Current password" type="password" autocomplete="current-password" required bind:value={current} error={fieldErrors.current_password} />
		<Field label="New password" type="password" autocomplete="new-password" required bind:value={next} error={fieldErrors.new_password} />
		<Field label="Confirm new password" type="password" autocomplete="new-password" required bind:value={confirm} error={fieldErrors.confirm} />
		<div class="card-actions"><button class="btn btn-primary" type="submit" disabled={busy}>Change password</button></div>
	</form>
</div>
