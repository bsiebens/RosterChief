<script lang="ts">
	import { Smartphone } from '@lucide/svelte';
	import QRCode from 'qrcode';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { auth, splitErrors, withReauth } from '#lib/api.ts';
	import type { Authenticator } from '#lib/types.ts';
	import Alert from './Alert.svelte';
	import Modal from './Modal.svelte';
	import OtpInput from './OtpInput.svelte';

	let { authenticator, onchange, onnewcodes }: { authenticator?: Extract<Authenticator, { type: 'totp' }>; onchange: () => void; onnewcodes: () => void } = $props();

	let setupOpen = $state(false);
	let removeOpen = $state(false);
	let secret = $state('');
	let qr = $state('');
	let code = $state('');
	let error = $state('');
	let busy = $state(false);

	async function begin() {
		error = code = '';
		busy = true;
		const r = await withReauth(() => auth('GET', '/account/authenticators/totp'));
		busy = false;
		if (r.meta?.secret) {
			secret = r.meta.secret;
			qr = await QRCode.toDataURL(r.meta.totp_url, { margin: 1, width: 192 });
			setupOpen = true;
		} else if (r.status !== 401) {
			error = splitErrors(r).form[0] ?? t('account.totp.err_start');
		}
	}

	async function activate(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		const r = await withReauth(() => auth('POST', '/account/authenticators/totp', { code: code.trim() }));
		busy = false;
		if (r.ok) {
			setupOpen = false;
			onchange();
			onnewcodes(); // allauth issues recovery codes with the first factor
		} else {
			const e = splitErrors(r);
			error = e.fields.code ?? e.form[0] ?? t('account.totp.err_code');
			code = '';
		}
	}

	async function remove() {
		busy = true;
		error = '';
		const r = await withReauth(() => auth('DELETE', '/account/authenticators/totp'));
		busy = false;
		if (r.ok) {
			removeOpen = false;
			onchange();
		} else error = splitErrors(r).form[0] ?? t('account.totp.err_remove');
	}
</script>

<div class="card">
	<div class="card-body">
		<div class="card-title">
			<Smartphone size={16} aria-hidden="true" /> {t('account.totp.title')}
			{#if authenticator}<span class="badge badge-success ml-auto">{t('account.badge.active')}</span>{:else}<span class="badge badge-ghost ml-auto">{t('account.badge.off')}</span>{/if}
		</div>
		<p class="text-sm text-muted">{t('account.totp.lead')}</p>
		{#if authenticator}
			<p class="font-mono text-xs text-muted">
				{t('account.added', { date: new Date(authenticator.created_at * 1000).toLocaleDateString(getLang()) })}{authenticator.last_used_at ? ` · ${t('account.last_used', { date: new Date(authenticator.last_used_at * 1000).toLocaleDateString(getLang()) })}` : ''}
			</p>
		{/if}
		{#if error && !setupOpen && !removeOpen}<Alert kind="error">{error}</Alert>{/if}
		<div class="card-actions mt-2">
			{#if authenticator}
				<button class="btn btn-outline btn-error btn-sm" onclick={() => ((removeOpen = true), (error = ''))}>{t('account.remove')}</button>
			{:else}
				<button class="btn btn-primary btn-sm" disabled={busy} onclick={begin}>{t('account.setup')}</button>
			{/if}
		</div>
	</div>
</div>

<Modal bind:open={setupOpen} title={t('account.totp.setup_title')}>
	<form class="flex flex-col gap-4" onsubmit={activate}>
		<ol class="list-decimal space-y-1 pl-5 text-sm">
			<li>{t('account.totp.step_scan')}</li>
			<li>{t('account.totp.step_code')}</li>
		</ol>
		<div class="flex flex-col items-center gap-2">
			{#if qr}<img src={qr} alt={t('account.totp.qr_alt')} width="192" height="192" class="rounded border border-edge" />{/if}
			<details class="text-center text-xs text-muted">
				<summary class="cursor-pointer">{t('account.totp.manual')}</summary>
				<code class="mt-1 block font-mono text-sm break-all text-ink select-all">{secret}</code>
			</details>
		</div>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<OtpInput bind:value={code} label={t('account.totp.code_label')} autofocus />
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (setupOpen = false)}>{t('common.cancel')}</button>
			<button type="submit" class="btn btn-primary" disabled={busy || code.trim().length < 6}>{t('account.totp.activate')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={removeOpen} title={t('account.totp.remove_title')}>
	<p class="text-sm">{t('account.totp.remove_body')}</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (removeOpen = false)}>{t('account.keep')}</button>
		<button class="btn btn-error" disabled={busy} onclick={remove}>{t('account.remove')}</button>
	</div>
</Modal>
