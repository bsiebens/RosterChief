<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { ArrowLeft, KeyRound, Pencil, Trash2, UserMinus, UserPlus, Baby, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import Field from '#lib/components/Field.svelte';
	import GrantResult from '#lib/components/members/detail/GrantResult.svelte';
	import GrantLoginModal from '#lib/components/members/detail/GrantLoginModal.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import AddFamilyPersonModal from '#lib/components/families/AddFamilyPersonModal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { familyRoleLabel } from '#lib/members/detail-helpers.ts';
	import { FAMILY_ROLES } from '#lib/families/roles.ts';

	import type { ActionData, PageData } from '../../../routes/(app)/families/[id]/$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const family = $derived(data.family);
	let renameOpen = $state(false);
	let deleteOpen = $state(false);
	let parentOpen = $state(false);
	let childOpen = $state(false);
	let busy = $state(false);
	let grantTarget = $state<{ id: string; name: string; email: string } | null>(null);
	let grantOpen = $state(false);
	$effect(() => {
		if (grantTarget) grantOpen = true;
	});
	$effect(() => {
		if (!grantOpen) grantTarget = null;
	});
	let toDelete = $state<{ id: string; name: string; has_login: boolean } | null>(null);
	let deletePersonOpen = $state(false);
	const errorFor = (action: string) => (form?.familyAction === action ? form.familyError : undefined);
	const tableError = $derived(['setRole', 'removeMember', 'grantLogin', 'deleteMember'].map(errorFor).find(Boolean));
	const sorted = $derived([...family.members].sort((a, b) => `${a.last_name} ${a.first_name}`.localeCompare(`${b.last_name} ${b.first_name}`)));
	const ids = $derived(family.members.map((m) => m.member_id));

	usePageHeader(() => ({ title: family?.display_name ?? t('families.title'), context: t('families.detail.context'), actions }));

	const submit = (after?: () => void) => () => {
		busy = true;
		return async ({ result, update }: { result: { type: string }; update: (o?: { reset?: boolean }) => Promise<void> }) => {
			await update({ reset: result.type === 'success' });
			busy = false;
			if (result.type === 'success') after?.();
		};
	};
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/families"><ArrowLeft size={16} /> {t('families.back')}</a>
	<button class="btn btn-outline gap-2" type="button" onclick={() => (renameOpen = true)}><Pencil size={16} /> {t('families.rename')}</button>
	<button class="btn btn-outline btn-error gap-2" type="button" onclick={() => (deleteOpen = true)}><Trash2 size={16} /> {t('families.delete')}</button>
{/snippet}

<svelte:head><title>{pageTitle(family?.display_name ?? t('families.title'))}</title></svelte:head>

{#if family}
		{#if form?.granted}<GrantResult granted={form.granted} />{/if}

	<div class="card card-body">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<h2 class="card-title text-club-dark"><Users size={18} /> {family.display_name} <InfoTip label={t('families.info_label')} text={t('families.guardian_rule')} /></h2>
			<div class="flex flex-wrap gap-2">
				<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (parentOpen = true)}><UserPlus size={16} /> {t('families.add_parent')}</button>
				<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (childOpen = true)}><Baby size={16} /> {t('families.add_child')}</button>
			</div>
		</div>

		{#if tableError}<Alert kind="error" title={t('families.detail.action_failed')}>{tableError}</Alert>{/if}

		<div class="overflow-x-auto">
			<table class="table">
				<thead>
					<tr><th>{t('families.detail.col.name')}</th><th>{t('families.detail.col.email')}</th><th>{t('families.detail.col.type')}</th><th></th></tr>
				</thead>
				<tbody>
					{#each sorted as m (m.member_id)}
						{@const name = `${m.first_name} ${m.last_name}`.trim()}
						<tr>
							<td class="font-semibold"><a class="link" href="/members/{m.member_id}">{m.last_name}, {m.first_name}</a></td>
							<td class="text-sm text-muted">{m.email || '—'}</td>
							<td>
								<form method="POST" action="?/setRole" use:enhance={submit()}>
									<input type="hidden" name="member_id" value={m.member_id} />
									<select name="role" class="select select-sm w-auto" aria-label={t('families.role_in_family')} value={m.role} onchange={(e) => e.currentTarget.form?.requestSubmit()}>
										{#each FAMILY_ROLES as r (r)}<option value={r}>{familyRoleLabel(r)}</option>{/each}
									</select>
								</form>
							</td>
							<td>
								<div class="flex flex-wrap items-center justify-end gap-1.5">
									{#if !m.has_login}
										<button class="btn btn-outline btn-xs gap-1" type="button" aria-label={t('families.detail.grant_named', { name })} onclick={() => (grantTarget = { id: m.member_id, name, email: m.email ?? '' })}><KeyRound size={13} /> {t('families.detail.grant_login')}</button>
									{/if}
									<a class="btn btn-outline btn-xs gap-1" href="/members/{m.member_id}/edit" aria-label={t('families.detail.edit_named', { name })}><Pencil size={13} /> {t('common.edit')}</a>
									<form method="POST" action="?/removeMember" use:enhance={submit()}>
										<input type="hidden" name="member_id" value={m.member_id} />
										<button class="btn btn-outline btn-error btn-xs gap-1" type="submit" disabled={busy} aria-label={t('families.remove_person', { name })}><UserMinus size={13} /> {t('families.remove')}</button>
									</form>
									<button
										class="btn btn-outline btn-error btn-xs gap-1"
										type="button"
										onclick={() => {
											toDelete = { id: m.member_id, name, has_login: !!m.has_login };
											deletePersonOpen = true;
										}}><Trash2 size={13} /> {t('families.detail.delete_person')}</button>
								</div>
							</td>
						</tr>
					{:else}
						<tr><td colspan="4" class="text-sm text-muted">{t('families.no_people')}</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>

	<AddFamilyPersonModal bind:open={parentOpen} role="parent" action="?/addMember" exclude={ids} error={errorFor('addMember')} fields={form?.familyAction === 'addMember' ? (form.familyFields ?? {}) : {}} />
	<AddFamilyPersonModal bind:open={childOpen} role="child" action="?/addMember" exclude={ids} error={errorFor('addMember')} fields={form?.familyAction === 'addMember' ? (form.familyFields ?? {}) : {}} />

	{#if grantTarget}
		<GrantLoginModal bind:open={grantOpen} memberId={grantTarget.id} name={grantTarget.name} email={grantTarget.email} error={errorFor('grantLogin')} fields={form?.familyAction === 'grantLogin' ? (form.familyFields ?? {}) : {}} />
	{/if}

	<Modal bind:open={renameOpen} title={t('families.rename')}>
		<form method="POST" action="?/rename" class="grid gap-3" use:enhance={submit(() => (renameOpen = false))}>
			{#if errorFor('rename')}<Alert kind="error">{errorFor('rename')}</Alert>{/if}
			<Field label={t('families.name')} name="name" value={family.name} help={t('families.name_help')} autocomplete="off" />
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (renameOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
			</div>
		</form>
	</Modal>

	<Modal bind:open={deleteOpen} title={t('families.delete')}>
		<form method="POST" action="?/delete" use:enhance={submit()}>
			{#if errorFor('delete')}<div class="mb-3"><Alert kind="error">{errorFor('delete')}</Alert></div>{/if}
			<p class="text-sm">{t('families.delete_body', { name: family.display_name })}</p>
			<div class="modal-action mt-4 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('families.delete')}</button>
			</div>
		</form>
	</Modal>

	<Modal bind:open={deletePersonOpen} title={t('families.detail.delete_person_title')}>
		{#if toDelete}
			<form method="POST" action="?/deleteMember" use:enhance={submit(() => (deletePersonOpen = false))}>
				<input type="hidden" name="member_id" value={toDelete.id} />
				{#if errorFor('deleteMember')}<div class="mb-3"><Alert kind="error" title={t('families.detail.delete_person_failed')}>{errorFor('deleteMember')}</Alert></div>{/if}
				<p class="text-sm">{t('families.detail.delete_person_body', { name: toDelete.name })}</p>
				{#if toDelete.has_login}
					<label class="label mt-3 cursor-pointer items-start justify-start gap-3">
						<input type="checkbox" name="detach_login" class="checkbox mt-0.5" />
						<span>
							<span class="label-text block">{t('members.form.detach_login')}</span>
							<span class="label-text-alt block">{t('members.form.detach_login_help')}</span>
						</span>
					</label>
				{/if}
				<div class="modal-action mt-4 flex justify-end gap-2">
					<button class="btn btn-outline" type="button" onclick={() => (deletePersonOpen = false)}>{t('common.cancel')}</button>
					<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('families.detail.delete_person')}</button>
				</div>
			</form>
		{/if}
	</Modal>
{/if}
