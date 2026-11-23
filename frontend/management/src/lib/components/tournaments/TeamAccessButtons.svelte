<script lang="ts">
	import { Check, KeyRound, Link2, UserPen } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { invalidateAll } from '$app/navigation';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';

	/**
	 * A team's private page (roster, logo, messages with the organisers) is reached with a link and a password. From here the organisers
	 * can copy the link, email the link with a NEW password to the contact person (the old password stops working), or change who the
	 * contact person is. `kind` says whether `id` is a team of the tournament or an application (a pending one has no team yet).
	 */
	let {
		kind,
		id,
		contact,
		compact = false,
		canManage = false
	}: { kind: 'team' | 'registration'; id: string; contact: { name?: string; email?: string; phone?: string }; compact?: boolean; canManage?: boolean } = $props();
	const root = $derived(kind === 'team' ? `/tournaments/teams/${id}` : `/tournaments/registrations/${id}`);
	const size = $derived(compact ? 'btn-xs btn-square' : '');
	const icon = $derived(compact ? 13 : 16);

	let copied = $state(false);
	let sendOpen = $state(false);
	let editOpen = $state(false);
	let busy = $state(false);
	let error = $state('');
	let fields = $state<Record<string, string>>({});
	let sentTo = $state('');
	let f = $state({ contact_name: '', contact_email: '', contact_phone: '' });

	async function copy() {
		const res = await api<{ url: string }>('POST', `${root}/private-link`);
		if (!res.ok || !res.data) {
			error = res.message || t('common.error.generic');
			sendOpen = true;
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
		const res = await api<{ email: string }>('POST', kind === 'team' ? `${root}/private-link/send` : `${root}/access/send`);
		busy = false;
		if (res.ok) sentTo = res.data?.email ?? contact.email ?? '';
		else error = res.code === 'no_email' ? t('tn.link.no_email') : res.message || t('common.error.generic');
	}

	function openEdit() {
		f = { contact_name: contact.name ?? '', contact_email: contact.email ?? '', contact_phone: contact.phone ?? '' };
		error = '';
		fields = {};
		editOpen = true;
	}
	async function saveContact(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = '';
		fields = {};
		const res = await api('PATCH', `${root}/contact`, { body: { contact_name: f.contact_name.trim(), contact_email: f.contact_email.trim(), contact_phone: f.contact_phone.trim() } });
		busy = false;
		if (res.ok) {
			editOpen = false;
			await invalidateAll();
		} else {
			fields = res.fields;
			error = Object.keys(res.fields).length ? '' : res.message || t('common.error.generic');
		}
	}
</script>

{#if kind === 'team'}
	<button class="btn btn-outline {size} gap-1.5" type="button" onclick={copy} title={t('tn.link.copy_title')} aria-label={t('tn.link.copy_title')}>
		{#if copied}<Check size={icon} aria-hidden="true" />{#if !compact} {t('tn.link.copied')}{/if}{:else}<Link2 size={icon} aria-hidden="true" />{#if !compact} {t('tn.link.copy')}{/if}{/if}
	</button>
{/if}
<button class="btn btn-outline {size} gap-1.5" type="button" onclick={() => { error = ''; sentTo = ''; sendOpen = true; }} title={t('tn.link.send_title')} aria-label={t('tn.link.send_title')}>
	<KeyRound size={icon} aria-hidden="true" />{#if !compact} {t('tn.link.send')}{/if}
</button>
{#if canManage}
	<button class="btn btn-outline {size} gap-1.5" type="button" onclick={openEdit} title={t('tn.link.contact_title')} aria-label={t('tn.link.contact_title')}><UserPen size={icon} aria-hidden="true" />{#if !compact} {t('tn.link.contact')}{/if}</button>
{/if}

<Modal bind:open={sendOpen} title={t('tn.link.send_title')}>
	<div class="grid gap-3">
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		{#if sentTo}
			<Alert kind="success">{t('tn.link.sent', { email: sentTo })}</Alert>
		{:else}
			<p>{contact.email ? t('tn.link.confirm', { email: contact.email }) : t('tn.link.confirm_unknown')}</p>
			<p class="text-sm text-muted">{t('tn.link.new_password')}</p>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (sendOpen = false)}>{sentTo ? t('common.close') : t('common.cancel')}</button>
			{#if !sentTo}<button class="btn btn-primary gap-2" type="button" disabled={busy || !contact.email} onclick={send}><KeyRound size={16} aria-hidden="true" /> {t('tn.link.send')}</button>{/if}
		</div>
	</div>
</Modal>

<Modal bind:open={editOpen} title={t('tn.link.contact_title')}>
	<form class="grid gap-3" onsubmit={saveContact}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label={t('tn.teams.f.contact_name')} bind:value={f.contact_name} error={fields.contact_name} required />
		<Field label={t('tn.teams.f.contact_email')} type="email" bind:value={f.contact_email} error={fields.contact_email} required />
		<Field label={t('tn.teams.f.contact_phone')} type="tel" bind:value={f.contact_phone} error={fields.contact_phone} />
		<p class="text-xs text-muted">{t('tn.link.contact_help')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (editOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
