<script lang="ts">
	import { auth, splitErrors } from '#lib/api.ts';
	import { t } from '#lib/i18n.svelte.ts';
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

<AuthCard title={t('auth.reset.title')} lead={t('auth.reset.lead')}>
	{#if sent}
		<Alert kind="success" title={t('auth.reset.sent_title')}>{t('auth.reset.sent_body', { email })}</Alert>
	{:else}
		{#each errors as message (message)}<Alert kind="error">{message}</Alert>{/each}
		<form class="flex flex-col gap-4" onsubmit={submit}>
			<Field label={t('auth.email')} type="email" name="email" autocomplete="email" required bind:value={email} error={fieldError} />
			<button class="btn btn-primary w-full" type="submit" disabled={busy}>{t('auth.reset.submit')}</button>
		</form>
	{/if}

	{#snippet footer()}<a href="/login">{t('auth.back_to_login')}</a>{/snippet}
</AuthCard>
