<script lang="ts">
	import { ChevronRight, KeyRound } from '@lucide/svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate } from '#lib/members/detail-helpers.ts';
	import { feeVariant, statusVariant, type MemberRow } from '#lib/members/list.ts';

	let { member, canManage, showFees, showStatus }: { member: MemberRow; canManage: boolean; showFees: boolean; showStatus: boolean } = $props();
	const name = $derived(`${member.last_name}, ${member.first_name}`);
	const n = $derived(member.family_ids.length);
</script>

<tr class="hover:bg-paper">
	<td>
		<div class="flex items-center gap-2.5">
			<Avatar first_name={member.first_name} last_name={member.last_name} photoUrl={member.photo_url} size="h-9 w-9" text="text-sm" />
			<div>
				<div class="flex items-center gap-1.5">
					<a class="link link-hover font-semibold text-ink" href="/members/{member.id}">{name}</a>
				</div>
				{#if member.date_of_birth}<div class="font-mono text-xs text-muted">{formatDate(member.date_of_birth)}</div>{/if}
			</div>
		</div>
	</td>
	{#if canManage}
		<td class="text-center align-middle">
			{#if member.has_login === false}
				<span class="badge badge-warning badge-sm gap-1" title={t('members.list.no_login_hint')}><KeyRound size={11} /> {t('members.list.no_login')}</span>
			{/if}
		</td>
	{/if}
	<td>
		<div class="flex flex-wrap gap-1">
			{#each member.roles as role (role)}
				<span class="badge badge-ghost badge-sm">{t(`members.list.role.${role}`)}</span>
			{:else}
				<span class="text-dim">—</span>
			{/each}
		</div>
	</td>
	<td class="text-sm text-muted">{member.email || '—'}</td>
	<td class="font-mono text-xs text-muted">{member.license_number || '—'}</td>
	<td>
		{#if member.families?.length}
			<div class="flex flex-wrap gap-1">
				{#each member.families as f (f.id)}
					{#if canManage}
						<a class="badge badge-outline badge-sm" href="/families/{f.id}">{f.name}</a>
					{:else}
						<span class="badge badge-outline badge-sm">{f.name}</span>
					{/if}
				{/each}
			</div>
		{:else if n}
			<a class="btn btn-outline btn-xs" href="/members/{member.id}#families">{t(n === 1 ? 'members.list.family_n' : 'members.list.family_n_other', { n })}</a>
		{:else}
			<span class="text-dim">—</span>
		{/if}
	</td>
	{#if showStatus}
		<td>
			{#if member.status}
				<span class="badge badge-sm {statusVariant(member.status)}">{t(`members.list.status.${member.status}`)}</span>
			{:else}
				<span class="text-dim">—</span>
			{/if}
		</td>
	{/if}
	{#if showFees}
		<td>
			{#if member.fee_status}
				<span class="badge badge-sm {feeVariant(member.fee_status)}">{t(`members.list.fee.${member.fee_status}`)}</span>
			{:else}
				<span class="text-dim">—</span>
			{/if}
		</td>
	{/if}
	<td class="text-right">
		{#if canManage}
			<a class="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-rule hover:text-ink" href="/members/{member.id}" aria-label={t('members.list.edit_named', { name })} title={t('members.list.edit')}><ChevronRight size={18} /></a>
		{/if}
	</td>
</tr>
