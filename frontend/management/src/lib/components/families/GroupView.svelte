<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { ArrowLeft, Layers, Pencil, ShieldCheck, Trash2, UserMinus, UserPlus } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';

	import type { ActionData, PageData } from '../../../routes/(app)/groups/[id]/$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const group = $derived(data.group);
	let addOpen = $state(false);
	let renameOpen = $state(false);
	let deleteOpen = $state(false);
	let busy = $state(false);
	let memberId = $state('');
	const errorFor = (action: string) => (form?.groupAction === action ? (form.groupError as string) : undefined);
	const tableError = $derived(errorFor('removeMember') ?? errorFor('updateMember'));
	let flagForms: Record<string, HTMLFormElement | undefined> = $state({});

	usePageHeader(() => ({ title: group?.name ?? t('groups.title'), context: t('groups.detail.context'), actions }));

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
	<a class="btn btn-outline gap-2" href="/groups"><ArrowLeft size={16} /> {t('groups.back')}</a>
	{#if group?.can_manage}
		<button class="btn btn-outline gap-2" type="button" onclick={() => (renameOpen = true)}><Pencil size={16} /> {t('groups.rename')}</button>
		<button class="btn btn-outline btn-error gap-2" type="button" onclick={() => (deleteOpen = true)}><Trash2 size={16} /> {t('groups.delete')}</button>
	{/if}
{/snippet}

<svelte:head><title>{pageTitle(group?.name ?? t('groups.title'))}</title></svelte:head>

{#if group}
	<div class="card card-body">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.06em] text-club-dark uppercase"><Layers size={16} /> {group.name} <span class="font-mono text-xs font-normal text-muted">· {group.members.length}</span></h2>
			{#if group.can_manage_members}<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (addOpen = true)}><UserPlus size={16} /> {t('groups.add_member')}</button>{/if}
		</div>
		<p class="flex items-start gap-2 text-xs text-muted"><ShieldCheck size={14} class="mt-0.5 shrink-0" aria-hidden="true" /> {t('groups.flags_help')}</p>
		{#if tableError}<Alert kind="error">{tableError}</Alert>{/if}
		<table class="table mt-2">
			<thead>
				<tr>
					<th>{t('families.card.name')}</th><th>{t('families.card.email')}</th>
					<th class="text-center">{t('groups.col.admin')}</th><th class="text-center">{t('groups.col.responds')}</th><th></th>
				</tr>
			</thead>
			<tbody>
				{#each group.members as m (m.member_id)}
					{@const name = `${m.last_name}, ${m.first_name}`}
					<tr>
						<td><a class="link font-semibold" href="/members/{m.member_id}">{name}</a></td>
						<td class="text-sm text-muted">{m.email || '—'}</td>
						<td class="text-center">
							<form method="POST" action="?/updateMember" bind:this={flagForms[`a${m.member_id}`]} use:enhance={submit()}>
								<input type="hidden" name="member_id" value={m.member_id} />
								<input type="hidden" name="is_admin" value={String(!m.is_admin)} />
								<input type="checkbox" class="checkbox checkbox-sm" checked={m.is_admin} disabled={busy || !group.can_manage} aria-label={t('groups.admin_named', { name })} onchange={() => flagForms[`a${m.member_id}`]?.requestSubmit()} />
							</form>
						</td>
						<td class="text-center">
							<form method="POST" action="?/updateMember" bind:this={flagForms[`r${m.member_id}`]} use:enhance={submit()}>
								<input type="hidden" name="member_id" value={m.member_id} />
								<input type="hidden" name="must_respond" value={String(!m.must_respond)} />
								<input type="checkbox" class="checkbox checkbox-sm" checked={m.must_respond} disabled={busy || !group.can_manage_members} aria-label={t('groups.responds_named', { name })} onchange={() => flagForms[`r${m.member_id}`]?.requestSubmit()} />
							</form>
						</td>
						<td>
							<div class="flex items-center justify-end gap-1.5">
								{#if data.me?.can_manage_members}<a class="btn btn-outline btn-xs btn-square" href="/members/{m.member_id}/edit" aria-label={t('families.detail.edit_named', { name })} title={t('common.edit')}><Pencil size={13} /></a>{/if}
								<form method="POST" action="?/removeMember" use:enhance={submit()}>
									<input type="hidden" name="member_id" value={m.member_id} />
									{#if group.can_manage_members}<button class="btn btn-outline btn-error btn-xs btn-square" type="submit" disabled={busy} aria-label={t('groups.remove_named', { name })} title={t('families.remove')}><UserMinus size={13} /></button>{/if}
								</form>
							</div>
						</td>
					</tr>
				{:else}
					<tr><td colspan="5" class="py-6 text-center text-muted">{t('groups.no_members')}</td></tr>
				{/each}
			</tbody>
		</table>
	</div>

	<Modal bind:open={addOpen} title={t('groups.add_member')}>
		<form method="POST" action="?/addMember" class="grid gap-3" use:enhance={submit(() => ((memberId = ''), (addOpen = false)))}>
			{#if errorFor('addMember')}<Alert kind="error">{errorFor('addMember')}</Alert>{/if}
			<EntityPicker kind="member" name="member_id" label={t('families.person')} exclude={group.members.map((m) => m.member_id)} bind:value={memberId} />
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary gap-2" type="submit" disabled={busy || !memberId}><UserPlus size={16} /> {t('groups.add_member_submit')}</button>
			</div>
		</form>
	</Modal>

	<Modal bind:open={renameOpen} title={t('groups.rename')}>
		<form method="POST" action="?/rename" class="grid gap-3" use:enhance={submit(() => (renameOpen = false))}>
			{#if errorFor('rename') && !form?.groupFields?.name}<Alert kind="error">{errorFor('rename')}</Alert>{/if}
			<Field label={t('groups.name')} name="name" value={group.name} required error={form?.groupAction === 'rename' ? form?.groupFields?.name : undefined} autocomplete="off" />
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (renameOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary" type="submit" disabled={busy}>{t('common.save')}</button>
			</div>
		</form>
	</Modal>

	<Modal bind:open={deleteOpen} title={t('groups.delete')}>
		<form method="POST" action="?/delete" use:enhance={submit()}>
			{#if errorFor('delete')}<div class="mb-3"><Alert kind="error">{errorFor('delete')}</Alert></div>{/if}
			<p class="text-sm">{t('groups.delete_body', { name: group.name })}</p>
			<div class="modal-action mt-4 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('groups.delete')}</button>
			</div>
		</form>
	</Modal>
{/if}
