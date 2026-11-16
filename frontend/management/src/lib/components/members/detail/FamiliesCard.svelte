<script lang="ts">
	import { enhance } from '$app/forms';
	import { House, Plus, UserMinus, UserPlus, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import AddFamilyPersonModal from '#lib/components/families/AddFamilyPersonModal.svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { FAMILY_ROLES } from '#lib/families/roles.ts';
	import { familyRoleLabel } from '#lib/members/detail-helpers.ts';
	import type { FamilyDetail, MemberDetail } from '#lib/members/detail-types.ts';

	let { member, families, isAdmin = false, form }: { member: MemberDetail; families: FamilyDetail[]; isAdmin?: boolean; form?: any } = $props();
	let open = $state(false);
	let mode = $state<'existing' | 'new'>('existing');
	let familyId = $state('');
	let busy = $state(false);
	let addTarget = $state<{ familyId: string; role: 'parent' | 'child' } | null>(null);
	let addOpen = $state(false);
	const openAdd = (familyId: string, role: 'parent' | 'child') => {
		addTarget = { familyId, role };
		addOpen = true;
	};
	const error = (action: string) => (form?.familyAction === action ? (form.familyError as string) : undefined);
	const run = (after?: () => void) => () => {
		busy = true;
		return async ({ result, update }: { result: { type: string }; update: () => Promise<void> }) => {
			await update();
			busy = false;
			if (result.type === 'success') after?.();
		};
	};
	const done = () => {
		open = false;
		familyId = '';
	};
</script>

<div class="card card-body">
	<div class="flex items-center justify-between gap-2">
		<h2 class="card-title"><House size={18} /> {t('members.detail.families')}</h2>
		{#if isAdmin}
			<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (open = true)}><Plus size={16} /> {t('families.member.add')}</button>
		{/if}
	</div>
	{#if error('leaveFamily')}<Alert kind="error">{error('leaveFamily')}</Alert>{/if}
	{#each member.families as link (link.id)}
		{@const detail = families.find((f) => f.id === link.id)}
		<div class="mb-3">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h3 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.06em] text-club-dark uppercase">
					<Users size={16} />
					{#if isAdmin}<a class="link" href="/families/{link.id}">{link.name}</a>{:else}<span>{link.name}</span>{/if}
				</h3>
				{#if isAdmin}
					<div class="flex flex-wrap items-center gap-2">
						<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => openAdd(link.id, 'parent')}><UserPlus size={16} /> {t('families.add_parent')}</button>
						<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => openAdd(link.id, 'child')}><UserPlus size={16} /> {t('families.add_child')}</button>
					</div>
				{/if}
			</div>
			<table class="table mt-2">
				<thead>
					<tr><th>{t('families.card.name')}</th><th>{t('families.card.email')}</th><th>{t('families.card.type')}</th>{#if isAdmin}<th></th>{/if}</tr>
				</thead>
				<tbody>
					{#each detail?.members ?? [] as m (m.member_id)}
						<tr>
							<td class="whitespace-nowrap"><a class="link {m.member_id === member.id ? 'font-bold' : 'font-semibold'}" href="/members/{m.member_id}" title="{m.first_name} {m.last_name}">{m.first_name}</a></td>
							<td class="text-sm text-muted">{m.email || '—'}</td>
							<td><span class="badge badge-sm badge-outline">{familyRoleLabel(m.role)}</span></td>
							{#if isAdmin}
								<td class="text-right">
									<form method="POST" action="?/leaveFamily" use:enhance={run()}>
										<input type="hidden" name="family_id" value={link.id} />
										<input type="hidden" name="member_id" value={m.member_id} />
										<button
											class="btn btn-outline btn-error btn-xs btn-square"
											type="submit"
											disabled={busy}
											title={t('families.card.remove_from', { name: m.first_name })}
											aria-label={t('families.card.remove_from', { name: `${m.first_name} ${m.last_name}` })}><UserMinus size={14} /></button>
									</form>
								</td>
							{/if}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<p class="text-sm text-muted">{t('members.detail.no_families')}</p>
	{/each}
</div>

{#snippet modeSwitch()}
	<div class="flex w-fit items-center gap-1 " role="group" aria-label={t('families.member.add_title')}>
		{#each [['existing', t('families.member.existing')], ['new', t('families.member.new')]] as [key, label] (key)}
			<button
				type="button"
				aria-pressed={mode === key}
				onclick={() => (mode = key as 'existing' | 'new')}
				class="rounded-md px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {mode === key ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}">{label}</button>
		{/each}
	</div>
{/snippet}

{#if isAdmin}
	<Modal bind:open title={t('families.member.add_title')} aside={modeSwitch}>
		<form method="POST" action={mode === 'existing' ? '?/addToFamily' : '?/newFamily'} class="grid gap-3" use:enhance={run(done)}>
			{#if error(mode === 'existing' ? 'addToFamily' : 'newFamily')}<Alert kind="error">{error(mode === 'existing' ? 'addToFamily' : 'newFamily')}</Alert>{/if}
			{#if mode === 'existing'}
				<EntityPicker kind="family" name="family_id" label={t('families.member.family')} exclude={member.families.map((f) => f.id)} bind:value={familyId} />
			{:else}
				<Field label={t('families.name')} name="name" help={t('families.name_help')} autocomplete="off" />
			{/if}
			<div class="form-control w-full">
				<label class="label" for="fc-role"><span class="label-text">{t('families.member.role')}</span></label>
				<select id="fc-role" name="role" class="select select-bordered w-full">
					{#each FAMILY_ROLES as r (r)}<option value={r}>{familyRoleLabel(r)}</option>{/each}
				</select>
			</div>
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary gap-2" type="submit" disabled={busy || (mode === 'existing' && !familyId)}><UserPlus size={16} /> {mode === 'existing' ? t('families.member.add_submit') : t('families.create')}</button>
			</div>
		</form>
	</Modal>
{/if}

{#if isAdmin && addTarget}
	<AddFamilyPersonModal
		bind:open={addOpen}
		role={addTarget.role}
		familyId={addTarget.familyId}
		action="?/addFamilyPerson"
		exclude={families.find((f) => f.id === addTarget!.familyId)?.members.map((m) => m.member_id) ?? []}
		error={error('addFamilyPerson')}
		fields={form?.familyAction === 'addFamilyPerson' ? (form.familyFields ?? {}) : {}}
	/>
{/if}
