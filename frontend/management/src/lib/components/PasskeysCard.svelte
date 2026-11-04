<script lang="ts">
	import { Pencil, Trash2, Usb } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { auth, createCredential, splitErrors, webauthnMessage, webauthnSupported, withReauth } from '#lib/api.ts';
	import type { Authenticator } from '#lib/types.ts';
	import Alert from './Alert.svelte';
	import Field from './Field.svelte';
	import Modal from './Modal.svelte';

	type Key = Extract<Authenticator, { type: 'webauthn' }>;
	let { keys, onchange, onnewcodes }: { keys: Key[]; onchange: () => void; onnewcodes: () => void } = $props();

	const supported = $derived(webauthnSupported());

	let addOpen = $state(false);
	let name = $state('');
	let passwordless = $state(false);
	let renaming = $state<Key | null>(null);
	let removing = $state<Key | null>(null);
	let error = $state('');
	let busy = $state(false);

	const fmt = (ts: number) => new Date(ts * 1000).toLocaleDateString(getLang());

	async function add(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = '';
		try {
			const begin = await withReauth(() => auth('GET', `/account/authenticators/webauthn${passwordless ? '?passwordless' : ''}`));
			if (!begin.ok) return (error = splitErrors(begin).form[0] ?? t('account.keys.err_start'));
			const credential = await createCredential(begin.data.creation_options);
			if (!credential) return;
			const r = await withReauth(() => auth('POST', '/account/authenticators/webauthn', { name: name.trim(), credential }));
			if (!r.ok) {
				const e = splitErrors(r);
				return (error = e.fields.name ?? e.form[0] ?? t('account.keys.err_add'));
			}
			addOpen = false;
			name = '';
			onchange();
			if (r.meta?.recovery_codes_generated) onnewcodes();
		} catch (e) {
			error = webauthnMessage(e);
		} finally {
			busy = false;
		}
	}

	async function rename(event: SubmitEvent) {
		event.preventDefault();
		if (!renaming) return;
		busy = true;
		error = '';
		const r = await withReauth(() => auth('PUT', '/account/authenticators/webauthn', { id: renaming!.id, name: name.trim() }));
		busy = false;
		if (r.ok) {
			renaming = null;
			onchange();
		} else error = splitErrors(r).fields.name ?? splitErrors(r).form[0] ?? t('account.keys.err_rename');
	}

	async function remove() {
		if (!removing) return;
		busy = true;
		error = '';
		const r = await withReauth(() => auth('DELETE', '/account/authenticators/webauthn', { authenticators: [removing!.id] }));
		busy = false;
		if (r.ok) {
			removing = null;
			onchange();
		} else error = splitErrors(r).form[0] ?? t('account.keys.err_remove');
	}
</script>

<div class="card">
	<div class="card-body">
		<div class="card-title">
			<Usb size={16} aria-hidden="true" /> {t('account.keys.title')}
			{#if keys.length}<span class="badge badge-success ml-auto">{t('account.keys.count', { n: keys.length })}</span>{:else}<span class="badge badge-ghost ml-auto">{t('account.badge.none')}</span>{/if}
		</div>
		<p class="text-sm text-muted">{t('account.keys.lead')}</p>

		{#if keys.length}
			<ul class="divide-y divide-rule">
				{#each keys as key (key.id)}
					<li class="flex items-center gap-3 py-2.5">
						<div class="min-w-0 flex-1">
							<div class="truncate text-sm font-semibold text-ink">{key.name}{#if key.is_passwordless} <span class="badge badge-info badge-sm ml-1">{t('account.keys.passkey')}</span>{/if}</div>
							<div class="font-mono text-[11px] text-muted">{t('account.added', { date: fmt(key.created_at) })}{key.last_used_at ? ` · ${t('account.last_used', { date: fmt(key.last_used_at) })}` : ` · ${t('account.never_used')}`}</div>
						</div>
						<button class="btn btn-ghost btn-square btn-sm" aria-label="{t('account.keys.rename')} {key.name}" onclick={() => ((renaming = key), (name = key.name), (error = ''))}><Pencil size={14} aria-hidden="true" /></button>
						<button class="btn btn-ghost btn-square btn-sm text-error hover:bg-danger-bg" aria-label="{t('account.remove')} {key.name}" onclick={() => ((removing = key), (error = ''))}><Trash2 size={14} aria-hidden="true" /></button>
					</li>
				{/each}
			</ul>
		{/if}

		{#if !supported}
			<Alert kind="info">{t('account.keys.unsupported')}</Alert>
		{/if}
		<div class="card-actions mt-2">
			<button class="btn btn-primary btn-sm" disabled={!supported} onclick={() => ((addOpen = true), (name = ''), (passwordless = false), (error = ''))}>{t('account.keys.add')}</button>
		</div>
	</div>
</div>

<Modal bind:open={addOpen} title={t('account.keys.add_title')}>
	<form class="flex flex-col gap-4" onsubmit={add}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label={t('account.keys.name')} placeholder={t('account.keys.name_placeholder')} maxlength={100} required bind:value={name} help={t('account.keys.name_help')} />
		<label class="flex cursor-pointer items-start gap-3 text-sm">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={passwordless} />
			<span><span class="font-semibold text-ink">{t('account.keys.create_passkey')}</span><br /><span class="text-muted">{t('account.keys.create_passkey_help')}</span></span>
		</label>
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
			<button type="submit" class="btn btn-primary" disabled={busy || !name.trim()}>{t('account.keys.continue')}</button>
		</div>
	</form>
</Modal>

<Modal open={!!renaming} title={t('account.keys.rename_title')} onclose={() => (renaming = null)}>
	<form class="flex flex-col gap-4" onsubmit={rename}>
		{#if error}<Alert kind="error">{error}</Alert>{/if}
		<Field label={t('account.keys.name')} maxlength={100} required bind:value={name} />
		<div class="modal-action mt-0">
			<button type="button" class="btn btn-outline" onclick={() => (renaming = null)}>{t('common.cancel')}</button>
			<button type="submit" class="btn btn-primary" disabled={busy || !name.trim()}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal open={!!removing} title={t('account.keys.remove_title')} onclose={() => (removing = null)}>
	<p class="text-sm">{t('account.keys.remove_body', { name: removing?.name ?? '' })}</p>
	{#if error}<div class="mt-3"><Alert kind="error">{error}</Alert></div>{/if}
	<div class="modal-action">
		<button class="btn btn-outline" onclick={() => (removing = null)}>{t('account.keep')}</button>
		<button class="btn btn-error" disabled={busy} onclick={remove}>{t('account.remove')}</button>
	</div>
</Modal>
