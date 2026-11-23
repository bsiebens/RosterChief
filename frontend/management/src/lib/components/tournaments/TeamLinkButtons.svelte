<script lang="ts">
	import { Check, Link2, Mail } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';

	/**
	 * The private page of a team (roster, logo, messages with the organisers): copy its link, or email it to the team's contact person.
	 * The page is made on first use for a team the organisers added themselves.
	 */
	let { teamId, email = '', compact = false }: { teamId: string; email?: string; compact?: boolean } = $props();
	const size = $derived(compact ? 'btn-xs' : '');
	let copied = $state(false);
	let confirmOpen = $state(false);
	let busy = $state(false);
	let error = $state('');
	let sentTo = $state('');

	async function copy() {
		error = '';
		const res = await api<{ url: string }>('POST', `/tournaments/teams/${teamId}/private-link`);
		if (!res.ok || !res.data) {
			error = res.message || t('common.error.generic');
			confirmOpen = true;
			return;
		}
		try {
			await navigator.clipboard.writeText(res.data.url);
			copied = true;
			setTimeout(() => (copied = false), 2500);
		} catch {
			window.prompt(t('tn.link.copy_manually'), res.data.url);
		}
	}

	async function send() {
		busy = true;
		error = '';
		const res = await api<{ email: string }>('POST', `/tournaments/teams/${teamId}/private-link/send`);
		busy = false;
		if (res.ok) sentTo = res.data?.email ?? email;
		else error = res.message || t('common.error.generic');
	}
</script>

<button class="btn btn-outline {size} gap-1.5" type="button" onclick={copy} title={t('tn.link.copy_title')}>
	{#if copied}<Check size={compact ? 13 : 16} aria-hidden="true" /> {t('tn.link.copied')}{:else}<Link2 size={compact ? 13 : 16} aria-hidden="true" /> {t('tn.link.copy')}{/if}
</button>
<button class="btn btn-outline {size} gap-1.5" type="button" onclick={() => { error = ''; sentTo = ''; confirmOpen = true; }} title={t('tn.link.send_title')}>
	<Mail size={compact ? 13 : 16} aria-hidden="true" /> {t('tn.link.send')}
</button>

<Modal bind:open={confirmOpen} title={t('tn.link.send_title')}>
	<div class="grid gap-3">
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		{#if sentTo}
			<Alert kind="success">{t('tn.link.sent', { email: sentTo })}</Alert>
		{:else}
			<p>{email ? t('tn.link.confirm', { email }) : t('tn.link.confirm_unknown')}</p>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (confirmOpen = false)}>{sentTo ? t('common.close') : t('common.cancel')}</button>
			{#if !sentTo}<button class="btn btn-primary gap-2" type="button" disabled={busy} onclick={send}><Mail size={16} aria-hidden="true" /> {t('tn.link.send')}</button>{/if}
		</div>
	</div>
</Modal>
