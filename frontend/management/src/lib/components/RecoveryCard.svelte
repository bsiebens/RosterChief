<script lang="ts">
	import { LifeBuoy } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { auth, splitErrors, withReauth } from '#lib/api.ts';
	import type { Authenticator } from '#lib/types.ts';
	import Alert from './Alert.svelte';
	import CodesDialog from './CodesDialog.svelte';
	import Modal from './Modal.svelte';

	let { authenticator, canGenerate, onchange, openRequest = 0 }: {
		authenticator?: Extract<Authenticator, { type: 'recovery_codes' }>;
		/** Recovery codes only make sense once another factor exists. */
		canGenerate: boolean;
		onchange: () => void;
		/** Bumped by the parent to show the codes straight after enrolment. */
		openRequest?: number;
	} = $props();

	let codesOpen = $state(false);
	let codes = $state<string[]>([]);
	let confirmOpen = $state(false);
	let error = $state('');
	let busy = $state(false);

	async function view() {
		error = '';
		busy = true;
		const r = await withReauth(() => auth('GET', '/account/authenticators/recovery-codes'));
		busy = false;
		if (r.ok) {
			codes = r.data?.unused_codes ?? [];
			codesOpen = true;
		} else if (r.status !== 401) error = splitErrors(r).form[0] ?? t('account.recovery.err_load');
	}

	async function generate() {
		busy = true;
		error = '';
		const r = await withReauth(() => auth('POST', '/account/authenticators/recovery-codes'));
		busy = false;
		if (r.ok) {
			confirmOpen = false;
			onchange();
			await view();
		} else error = splitErrors(r).form[0] ?? t('account.recovery.err_generate');
	}

	let lastRequest = 0;
	$effect(() => {
		if (openRequest > lastRequest) {
			lastRequest = openRequest;
			setTimeout(view, 300); // let the list reload so the card reflects the new codes
		}
	});
</script>

<div class="card">
	<div class="card-body">
		<div class="card-title">
			<LifeBuoy size={16} aria-hidden="true" /> {t('account.recovery.title')}
			{#if authenticator}<span class="badge badge-success ml-auto">{t('account.recovery.left', { n: authenticator.unused_code_count, total: authenticator.total_code_count })}</span>{:else}<span class="badge badge-ghost ml-auto">{t('account.badge.none')}</span>{/if}
		</div>
		<p class="text-sm text-muted">{t('account.recovery.lead')}</p>
		{#if authenticator && authenticator.unused_code_count <= 2}
			<Alert kind="warning">{t('account.recovery.low')}</Alert>
		{/if}
		{#if error && !codesOpen && !confirmOpen}<Alert kind="error">{error}</Alert>{/if}
		<div class="card-actions mt-2">
			{#if authenticator}
				<button class="btn btn-outline btn-sm" disabled={busy} onclick={view}>{t('account.recovery.view')}</button>
				<button class="btn btn-outline btn-sm" disabled={busy} onclick={() => ((confirmOpen = true), (error = ''))}>{t('account.recovery.new_set')}</button>
			{:else}
				<button class="btn btn-primary btn-sm" disabled={busy || !canGenerate} onclick={generate}>{t('account.recovery.generate_codes')}</button>
				{#if !canGenerate}<span class="self-center text-xs text-muted">{t('account.recovery.need_factor')}</span>{/if}
			{/if}
		</div>
	</div>
</div>

<CodesDialog bind:open={codesOpen} {codes} />

<Modal bind:open={confirmOpen} title={t('account.recovery.confirm_title')}>
	<p class="text-sm">{t('account.recovery.confirm_body')}</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (confirmOpen = false)}>{t('common.cancel')}</button>
		<button class="btn btn-primary" disabled={busy} onclick={generate}>{t('account.recovery.generate')}</button>
	</div>
</Modal>
