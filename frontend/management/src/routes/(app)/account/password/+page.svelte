<script lang="ts">
	import { auth, splitErrors, withReauth } from '#lib/api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { KeyRound } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';

	let current = $state('');
	let next = $state('');
	let confirm = $state('');
	let busy = $state(false);
	let done = $state(false);
	let errors = $state<string[]>([]);
	let fieldErrors = $state<Record<string, string>>({});

	usePageHeader(() => ({ title: t('account.password.title') }));

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		errors = [];
		fieldErrors = {};
		done = false;
		if (next !== confirm) {
			fieldErrors = { confirm: t('auth.password_mismatch') };
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

<svelte:head><title>{pageTitle(t('account.password.title'))}</title></svelte:head>

<div class="px-0 md:px-3 md:py-1"><div class="card max-w-xl">
	<div class="card-h"><KeyRound size={16} class="shrink-0" aria-hidden="true" /><h2>{t('account.password.title')}</h2></div>
	<form class="card-body gap-4" onsubmit={submit}>
		{#if done}<Alert kind="success">{t('account.password.done')}</Alert>{/if}
		{#each errors as message (message)}<Alert kind="error">{message}</Alert>{/each}
		<Field label={t('account.password.current')} type="password" autocomplete="current-password" required bind:value={current} error={fieldErrors.current_password} />
		<Field label={t('account.password.new')} type="password" autocomplete="new-password" required bind:value={next} error={fieldErrors.new_password} />
		<Field label={t('account.password.confirm')} type="password" autocomplete="new-password" required bind:value={confirm} error={fieldErrors.confirm} />
		<div class="card-actions"><button class="btn btn-primary" type="submit" disabled={busy}>{t('account.password.submit')}</button></div>
	</form>
</div>
</div>
