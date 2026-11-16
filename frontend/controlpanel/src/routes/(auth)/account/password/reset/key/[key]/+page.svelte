<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { auth, splitErrors } from '#lib/api.ts';
	import AuthCard from '#lib/components/AuthCard.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';

	let { data } = $props();

	let password = $state('');
	let confirm = $state('');
	let busy = $state(false);
	let errors = $state<string[]>([]);
	let fieldErrors = $state<Record<string, string>>({});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		errors = [];
		fieldErrors = {};
		if (password !== confirm) {
			fieldErrors = { confirm: 'The two passwords do not match.' };
			return;
		}
		busy = true;
		const r = await auth('POST', '/auth/password/reset', { key: page.params.key, password });
		// 401 = password set but a flow (verify email / MFA) is still pending; 200 = signed in. Either way the
		// password is changed, so send them to sign in cleanly.
		if (r.ok || r.status === 401) await goto('/login?reset=done', { refreshAll: true });
		else {
			const e = splitErrors(r);
			errors = e.form;
			fieldErrors = { ...e.fields, ...(e.fields.key ? {} : {}) };
			if (e.fields.key) errors = [e.fields.key];
		}
		busy = false;
	}
</script>

<AuthCard title="Choose a password" lead={data.valid && data.email ? `For ${data.email}` : undefined}>
	{#if !data.valid}
		<Alert kind="error" title="Link expired">This password link is invalid or has already been used.</Alert>
		<a class="btn btn-outline w-full" href="/account/password/reset">Request a new link</a>
	{:else}
		{#each errors as message (message)}<Alert kind="error">{message}</Alert>{/each}
		<form class="flex flex-col gap-4" onsubmit={submit}>
			<Field label="New password" type="password" name="password" autocomplete="new-password" required bind:value={password} error={fieldErrors.password} />
			<Field label="Confirm new password" type="password" name="confirm" autocomplete="new-password" required bind:value={confirm} error={fieldErrors.confirm} />
			<button class="btn btn-primary w-full" type="submit" disabled={busy}>Save password</button>
		</form>
	{/if}
</AuthCard>
