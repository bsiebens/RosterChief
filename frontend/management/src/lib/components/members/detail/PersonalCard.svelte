<script lang="ts">
	import { Phone, ShieldAlert, User, Users } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDate, telHref } from '#lib/members/detail-helpers.ts';
	import type { GuardianContact, MemberDetail } from '#lib/members/detail-types.ts';

	let { member, guardians = [] }: { member: MemberDetail; guardians?: GuardianContact[] } = $props();
	const dash = '—';
</script>

{#snippet row(label: string, value: string)}
	<div class="flex items-center justify-between gap-4 py-2">
		<dt class="text-sm text-muted">{label}</dt>
		<dd class="font-mono text-xs font-semibold text-ink">{value || dash}</dd>
	</div>
{/snippet}

<div class="card card-body">
	<h2 class="card-title"><User size={18} /> {t('members.detail.personal')}</h2>
	<dl>
		{@render row(t('members.detail.dob'), formatDate(member.date_of_birth))}
		{@render row(t('members.detail.phone'), member.phone_display ?? member.phone ?? '')}
		{@render row(t('members.detail.email'), member.email)}
		{@render row(t('members.detail.emergency_phone'), member.emergency_phone_display ?? member.emergency_phone ?? '')}
		{#each guardians as g (g.id)}
			{@render row(`${t('members.detail.parent_phone')} — ${g.name}`, g.phone_display ?? g.phone ?? '')}
			{@render row(`${t('members.detail.parent_emergency_phone')} — ${g.name}`, g.emergency_phone_display ?? g.emergency_phone ?? '')}
		{/each}
	</dl>

	<div class="mt-2 flex flex-wrap items-center gap-2">
		{#if member.phone}
			<a class="btn btn-outline btn-info btn-sm flex-1 gap-2" href={telHref(member.phone)}><Phone size={14} /> {t('members.detail.call')}</a>
		{/if}
		{#if member.emergency_phone}
			<a class="btn btn-outline btn-error btn-sm flex-1 gap-2" href={telHref(member.emergency_phone)}><ShieldAlert size={14} /> {t('members.detail.emergency_call')}</a>
		{/if}
	</div>

	{#if guardians.length}
		<div class="my-1 h-px bg-rule"></div>
		<div class="mb-1 flex items-center gap-1.5 font-display text-xs font-bold tracking-[.1em] text-muted uppercase"><Users size={12} /> {t('members.detail.guardian_contact')}</div>
		{#each guardians as g (g.id)}
			<div class="mt-2 flex flex-wrap items-center gap-2">
				{#if g.phone}<a class="btn btn-outline btn-info btn-sm flex-1 gap-2" href={telHref(g.phone)}><Phone size={14} /> {t('members.detail.call_name', { name: g.name })}</a>{/if}
				{#if g.emergency_phone}<a class="btn btn-outline btn-error btn-sm flex-1 gap-2" href={telHref(g.emergency_phone)}><ShieldAlert size={14} /> {t('members.detail.emergency_name', { name: g.name })}</a>{/if}
			</div>
		{/each}
	{/if}
</div>
