<script lang="ts">
	import { auth, splitErrors } from '#lib/api.ts';
	import AuthCard from '#lib/components/AuthCard.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';

	let email = $state('');
	let busy = $state(false);
	let sent = $state(false);
	let errors = $state<string[]>([]);
	let fieldError = $state('');

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		errors = [];
		fieldError = '';
		const r = await auth('POST', '/auth/password/request', { email });
		if (r.ok) sent = true;
		else {
			const e = splitErrors(r);
			errors = e.form;
			fieldError = e.fields.email ?? '';
		}
		busy = false;
	}
</script>

<AuthCard title="Reset password" lead="We'll email you a link to choose a new one.">
	{#if sent}
		<Alert kind="success" title="Check your inbox">If an account exists for {email}, a reset link is on its way.</Alert>
	{:else}
		{#each errors as message (message)}<Alert kind="error">{message}</Alert>{/each}
		<form class="flex flex-col gap-4" onsubmit={submit}>
			<Field label="Email" type="email" name="email" autocomplete="email" required bind:value={email} error={fieldError} />
			<button class="btn btn-primary w-full" type="submit" disabled={busy}>Send reset link</button>
		</form>
	{/if}

	{#snippet footer()}<a href="/login">Back to sign in</a>{/snippet}
</AuthCard>
