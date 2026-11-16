<script lang="ts">
	import { enhance } from '$app/forms';
	import { History, Banknote, BadgeCheck, HandCoins, IdCard, Plus, Trash2, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import RecordPaymentModal from '#lib/components/members/detail/RecordPaymentModal.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import AddMembershipModal from '#lib/components/members/detail/AddMembershipModal.svelte';
	import { formatDay } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { currentMembership, feeBadge, feeStatusLabel, roleInfoKeys, seasonName, statusBadge, statusLabel } from '#lib/members/detail-helpers.ts';
	import type { MemberDetail, Season } from '#lib/members/detail-types.ts';

	let { member, seasons, isAdmin = false, form }: { member: MemberDetail; seasons: Season[]; isAdmin?: boolean; form?: any } = $props();
	const currentSeason = $derived(seasons.find((s) => s.is_current));
	let addOpen = $state(false);
	const current = $derived(currentMembership(member.club_memberships, seasons));
	const infoKeys = $derived(roleInfoKeys(member, !!current));
	let payOpen = $state(false);
	const waived = $derived(current?.fee_status === 'waived');
	const money = (v?: string | null) => Number(v ?? 0).toFixed(2);
	const remaining = $derived(current ? Math.max(Number(current.fee_amount ?? 0) - Number(current.amount_paid ?? 0), 0).toFixed(2) : '0.00');
	const canPay = $derived(!!current && isAdmin && !waived && current.status !== 'cancelled' && Number(remaining) > 0);
	const canActivate = $derived(!!current && isAdmin && ['pending', 'lapsed'].includes(current.status));
	const canWaive = $derived(!!current && isAdmin && current.status !== 'cancelled');
	let busy = $state(false);
	// A season membership can only be deleted while it is pending and unpaid; anything further along is cancelled instead.
	let deleting = $state<MemberDetail['club_memberships'][number] | null>(null);
	let deleteOpen = $state(false);
	const deletable = (cm: MemberDetail['club_memberships'][number]) => isAdmin && cm.status === 'pending' && cm.fee_status === 'unpaid';
	const run = () => {
		busy = true;
		return async ({ update }: { update: () => Promise<void> }) => {
			await update();
			busy = false;
		};
	};
</script>

<div class="card card-body">
	<div class="flex items-center justify-between gap-2">
		<h2 class="card-title"><IdCard size={18} /> {t('members.detail.membership')}</h2>
		<div class="flex flex-wrap justify-end gap-2">
			{#if isAdmin && currentSeason && !current}
				<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (addOpen = true)}><Plus size={16} /> {t('members.detail.add_membership')}</button>
			{/if}
			{#if current}
				{#if canWaive}
					<form method="POST" action="?/waive" use:enhance={run}>
						<input type="hidden" name="membership_id" value={current.id} />
						<input type="hidden" name="waived" value={waived ? 'false' : 'true'} />
						<button class="btn btn-outline btn-sm" type="submit" disabled={busy}>{waived ? t('members.detail.unwaive') : t('members.detail.waive')}</button>
					</form>
				{/if}
				{#if canActivate}
					<form method="POST" action="?/activate" use:enhance={run}>
						<input type="hidden" name="membership_id" value={current.id} />
						<button class="btn btn-primary btn-sm gap-2" type="submit" disabled={busy}><BadgeCheck size={16} /> {t('members.detail.activate')}</button>
					</form>
				{/if}
			{/if}
		</div>
	</div>
	{#each infoKeys as key (key)}
		<div class="alert alert-warning"><Users size={16} /><span>{t(key)}</span></div>
	{/each}
	{#if current}
		<dl>
			<div class="flex items-center justify-between gap-4 py-2">
				<dt class="text-sm text-muted">{t('members.detail.license')}</dt>
				<dd class="font-mono text-xs font-semibold text-ink">{member.license_number || '—'}</dd>
			</div>
			<div class="flex items-center justify-between gap-4 py-2">
				<dt class="text-sm text-muted">{t('members.detail.status')}</dt>
				<dd><span class="badge badge-sm {statusBadge(current.status)}">{statusLabel(current.status)}</span></dd>
			</div>
			{#if isAdmin && current.fee_status}
				<div class="flex items-center justify-between gap-4 py-2">
					<dt class="text-sm text-muted">{t('members.detail.fee_status')}</dt>
					<dd class="flex flex-wrap justify-end gap-1.5">
						<span class="badge badge-sm {feeBadge(current.fee_status)}">{feeStatusLabel(current.fee_status)}</span>
						{#if current.next_installment?.late}<span class="badge badge-error badge-sm">{t('installments.late')}</span>{/if}
					</dd>
				</div>
			{/if}
			{#if isAdmin && current.next_installment}
				<div class="flex items-center justify-between gap-4 py-2">
					<dt class="text-sm text-muted">{t('installments.next_due')}</dt>
					<dd class="text-sm font-semibold text-ink">{formatDay(current.next_installment.due_date)} · {money(current.next_installment.remaining)}</dd>
				</div>
			{/if}
			{#if isAdmin && current.fee_amount != null}
				<div class="flex items-center justify-between gap-4 py-2">
					<dt class="text-sm text-muted">{t('members.detail.fee_paid')}</dt>
					<dd class="text-sm font-semibold text-ink">{money(current.amount_paid)} / {money(current.fee_amount)}</dd>
				</div>
			{/if}
		</dl>
		{#if isAdmin}
			{#if form?.membershipActionError}<Alert kind="error" title={t('members.detail.action.failed')}>{form.membershipActionError}</Alert>{/if}
			<div class="flex flex-wrap gap-2">
				{#if canPay}
					<button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => (payOpen = true)}><Banknote size={16} /> {t('members.detail.pay.record')}</button>
					<form method="POST" action="?/markPaid" use:enhance={run}>
						<input type="hidden" name="membership_id" value={current.id} />
						<button class="btn btn-outline btn-sm gap-2" type="submit" disabled={busy}><HandCoins size={16} /> {t('members.detail.pay.mark_paid', { amount: remaining })}</button>
					</form>
				{/if}
			</div>
		{/if}
	{:else if !infoKeys.length}
		<p class="text-sm text-muted">{t('members.detail.not_rostered')}</p>
	{/if}

	{#if member.club_memberships.length}
		<div class="my-1 h-px bg-rule"></div>
		<h3 class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase flex items-center gap-2"><History size={16} class="shrink-0" aria-hidden="true" /> {t('members.detail.season_history')}</h3>
		<table class="table">
			<thead>
				<tr>
					<th>{t('members.detail.season')}</th>
					<th>{t('members.detail.status')}</th>
					{#if isAdmin}<th>{t('members.detail.fee_status')}</th><th></th>{/if}
				</tr>
			</thead>
			<tbody>
				{#each member.club_memberships as cm (cm.id)}
					<tr>
						<td class="font-semibold">{seasonName(seasons, cm.season_id) || '—'}</td>
						<td>{statusLabel(cm.status)}</td>
						{#if isAdmin}
								<td>
									{cm.fee_status ? feeStatusLabel(cm.fee_status) : '—'}
									{#if cm.next_installment?.late}<span class="badge badge-error badge-sm">{t('installments.late')}</span>{/if}
								</td>
							<td class="text-right">
								{#if deletable(cm)}
									<button
										class="btn btn-outline btn-error btn-xs gap-1"
										type="button"
										title={t('members.detail.season_delete_hint')}
										aria-label="{t('members.detail.season_delete')} {seasonName(seasons, cm.season_id)}"
										onclick={() => {
											deleting = cm;
											deleteOpen = true;
										}}><Trash2 size={13} /> {t('members.detail.season_delete')}</button>
								{/if}
							</td>
						{/if}
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>

{#if isAdmin && currentSeason}
	<AddMembershipModal bind:open={addOpen} season={currentSeason} error={form?.membershipError} fields={form?.membershipFields ?? {}} />
{/if}

{#if isAdmin && current}
	<RecordPaymentModal bind:open={payOpen} membershipId={current.id} {remaining} error={form?.membershipAction === 'recordPayment' ? form?.membershipActionError : undefined} fields={form?.membershipAction === 'recordPayment' ? (form?.membershipActionFields ?? {}) : {}} />
{/if}

{#if isAdmin}
	<Modal bind:open={deleteOpen} title={t('members.detail.season_delete_title')}>
		<form
			method="POST"
			action="?/deleteMembership"
			class="grid gap-3"
			use:enhance={() => {
				busy = true;
				return async ({ update, result }) => {
					await update();
					busy = false;
					if (result.type === 'success') deleteOpen = false;
				};
			}}>
			<input type="hidden" name="membership_id" value={deleting?.id ?? ''} />
			{#if form?.membershipAction === 'deleteMembership' && form.membershipActionError}<Alert kind="error" title={t('members.detail.season_delete_failed')}>{form.membershipActionError}</Alert>{/if}
			<p>{t('members.detail.season_delete_body', { name: `${member.first_name} ${member.last_name}`.trim(), season: deleting ? seasonName(seasons, deleting.season_id) : '' })}</p>
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('members.detail.season_delete')}</button>
			</div>
		</form>
	</Modal>
{/if}
