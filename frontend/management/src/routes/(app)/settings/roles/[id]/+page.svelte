<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { ShieldCheck, ArrowLeft, Lock, Save, Trash2, Users, ClipboardList, Settings2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import PermissionPicker from '#lib/components/settings/PermissionPicker.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const role = $derived(data.role);
	const locked = $derived(role.is_owner);

	// svelte-ignore state_referenced_locally
	let name = $state(data.role.name);
	// svelte-ignore state_referenced_locally
	let description = $state(data.role.description);
	// svelte-ignore state_referenced_locally
	let selected = $state<string[]>([...data.role.permissions]);
	// A reload after a save brings the stored role: start from it again.
	$effect(() => {
		const stored = data.role;
		untrack(() => {
			name = stored.name;
			description = stored.description;
			selected = [...stored.permissions];
		});
	});
	const dirty = $derived(name !== role.name || description !== role.description || JSON.stringify([...selected].sort()) !== JSON.stringify([...role.permissions].sort()));

	let busy = $state(false);
	let deleteOpen = $state(false);
	let tried = $state(false);
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const op = $derived(form && 'op' in form ? String(form.op) : '');
	const fields = $derived((form && 'fields' in form ? form.fields : {}) as Record<string, string>);
	const failure = $derived(form && 'message' in form && op !== '' && !('done' in form) ? String(form.message || t('common.error.generic')) : '');

	usePageHeader(() => ({ title: t('roles.edit.title'), context: data.role.name, actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/settings/roles"><ArrowLeft size={16} /> {t('roles.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('roles.edit.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created && !dirty}<Alert kind="success">{t('roles.created')}</Alert>{/if}
	{#if done === 'save'}<Alert kind="success">{t('roles.saved')}</Alert>{/if}
	{#if failure}<Alert kind="error" title={t('roles.action_failed')}>{failure}</Alert>{/if}
	{#if locked}<Alert kind="info">{t('roles.owner_locked')}</Alert>{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<form
				id="role-form"
				method="POST"
				action="?/save"
				class="grid gap-5"
				novalidate
				use:enhance={({ cancel }) => {
					if (!name.trim()) {
						tried = true;
						cancel();
						return;
					}
					busy = true;
					return async ({ update }) => {
						await update({ reset: false });
						busy = false;
						tried = false;
					};
				}}>
				<section class="card card-body gap-3">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ClipboardList size={16} class="shrink-0" aria-hidden="true" /> {t('roles.card.details')}</h2>
					<div class="form-control">
						<label class="label" for="name"><span class="label-text">{t('roles.field.name')}</span></label>
						<input id="name" name="name" class="input input-bordered w-full {op === 'save' && fields.name || (tried && !name.trim()) ? 'input-error' : ''}" bind:value={name} maxlength="150" disabled={locked} />
						{#if (op === 'save' && fields.name) || (tried && !name.trim())}<span class="mt-1 block text-xs text-error">{fields.name || t('roles.err.name')}</span>{/if}
					</div>
					<div class="form-control">
						<label class="label" for="description"><span class="label-text">{t('roles.field.description')}</span></label>
						<input id="description" name="description" class="input input-bordered w-full" bind:value={description} maxlength="255" disabled={locked} />
					</div>
				</section>
				<section class="card card-body gap-3">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ShieldCheck size={16} class="shrink-0" aria-hidden="true" /> {t('roles.card.permissions')}</h2>
					<p class="text-xs text-muted">{t('roles.card.permissions_desc')}</p>
					<PermissionPicker permissions={data.permissions} bind:selected disabled={locked} />
				</section>
			</form>

		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Settings2 size={16} class="shrink-0" aria-hidden="true" /> {t('roles.card.actions')}</h2>
				<div class="flex flex-wrap items-center gap-2 text-xs text-muted">
					{#if role.is_system}<span class="badge badge-sm gap-1"><Lock size={10} aria-hidden="true" /> {t('roles.system')}</span>{/if}
					<span class="font-mono">{t('roles.counts', { permissions: locked ? t('roles.all') : role.permissions.length, people: role.member_count })}</span>
				</div>
				{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('forms.unsaved')}</p>{/if}
				<button class="btn btn-primary w-full gap-2" type="submit" form="role-form" disabled={busy || !dirty || locked}><Save size={16} /> {t('common.save')}</button>
				<ActionList>
					<ActionRow icon={Users} chevron href="/settings/roles?tab=people&open={role.id}">{t('roles.card.people', { n: role.member_count })}</ActionRow>
					{#if !role.is_system}<ActionRow icon={Trash2} danger onclick={() => (deleteOpen = true)}>{t('common.delete')}</ActionRow>{/if}
				</ActionList>
			</section>
		</aside>
	</div>
</div>

<Modal bind:open={deleteOpen} title={t('roles.delete.title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance>
		<p>{t('roles.delete.body', { name: role.name, n: role.member_count })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit"><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
