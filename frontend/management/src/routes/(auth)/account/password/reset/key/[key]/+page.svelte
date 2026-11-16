<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { auth, splitErrors } from '#lib/api.ts';
	import { t } from '#lib/i18n.svelte.ts';
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
			fieldErrors = { confirm: t('auth.password_mismatch') };
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

<AuthCard title={t('auth.choose.title')} lead={data.valid && data.email ? t('auth.choose.for', { email: data.email }) : undefined}>
	{#if !data.valid}
		<Alert kind="error" title={t('auth.link_expired')}>{t('auth.choose.expired_body')}</Alert>
		<a class="btn btn-outline w-full" href="/account/password/reset">{t('auth.choose.request_new')}</a>
	{:else}
		{#each errors as message (message)}<Alert kind="error">{message}</Alert>{/each}
		<form class="flex flex-col gap-4" onsubmit={submit}>
			<Field label={t('auth.choose.new_password')} type="password" name="password" autocomplete="new-password" required bind:value={password} error={fieldErrors.password} />
			<Field label={t('auth.choose.confirm_password')} type="password" name="confirm" autocomplete="new-password" required bind:value={confirm} error={fieldErrors.confirm} />
			<button class="btn btn-primary w-full" type="submit" disabled={busy}>{t('auth.choose.save')}</button>
		</form>
	{/if}
</AuthCard>
