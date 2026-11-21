<script lang="ts">
	import { FileDown, Plus, Trash2, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import TeamLogo from '#lib/components/tournaments/TeamLogo.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Toast from '#lib/components/Toast.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { headerSnapshot, Runner, TBASE, type TeamDoc } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const canManage = $derived(tn.can_manage);
	const canOperate = $derived(tn.can_operate);
	const run = new Runner();
	const base = $derived(`/tournaments/${tn.id}`);

	let addOpen = $state(false);
	let kind = $state<'club' | 'foreign'>('club');
	let clubTeamId = $state('');
	let foreign = $state({ foreign_name: '', foreign_contact_name: '', foreign_contact_email: '', foreign_contact_phone: '', foreign_primary_color: '', foreign_secondary_color: '' });

	let withdrawing = $state<TeamDoc | null>(null);
	let withdrawOpen = $state(false);
	let withdrawReason = $state('');
	let deleting = $state<TeamDoc | null>(null);
	let deleteOpen = $state(false);

	async function add(e: SubmitEvent) {
		e.preventDefault();
		const body = kind === 'club' ? { kind, team_id: clubTeamId } : { kind, ...foreign };
		if (await run.post(`${base}/teams`, body)) {
			addOpen = false;
			clubTeamId = '';
			foreign = { foreign_name: '', foreign_contact_name: '', foreign_contact_email: '', foreign_contact_phone: '', foreign_primary_color: '', foreign_secondary_color: '' };
		}
	}
	const setPayment = (team: TeamDoc, payment_status: string) => run.patch(`/tournaments/teams/${team.id}/payment`, { payment_status });
	async function withdraw() {
		if (withdrawing && (await run.post(`/tournaments/teams/${withdrawing.id}/withdraw`, { reason: withdrawReason.trim() }))) withdrawOpen = false;
	}
	async function remove() {
		if (deleting && (await run.del(`/tournaments/teams/${deleting.id}`))) deleteOpen = false;
	}
	async function uploadLogo(team: TeamDoc, file: File | undefined) {
		if (!file) return;
		const form = new FormData();
		form.set('logo', file);
		await run.run(() => api('POST', `/tournaments/teams/${team.id}/logo`, { form }));
	}
	const sorted = $derived([...data.rows].sort((a, b) => Number(!!a.withdrawn_at) - Number(!!b.withdrawn_at)));

	const hdr = headerSnapshot(() => ({ base, canOperate }));
	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.teams.count', { n: data.rows.filter((r) => !r.withdrawn_at).length }), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/api/v1{hdr.base}/lunch-summary/pdf" target="_blank" rel="noopener"><FileDown size={16} /> {t('tn.teams.lunch_pdf')}</a>
	{#if hdr.canOperate}<button class="btn btn-primary gap-2" type="button" onclick={() => { run.clear(); addOpen = true; }}><Plus size={16} /> {t('tn.teams.add')}</button>{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('tn.tab.teams'))}</title></svelte:head>

<div class="grid gap-4">
	{#if run.error && !addOpen && !withdrawOpen && !deleteOpen}<Alert kind="error">{run.error}</Alert>{/if}
	{#if data.error}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if !sorted.length}
		<div class="card card-body items-center py-10 text-center text-muted">
			<Users size={28} />
			<p>{t('tn.teams.empty')}</p>
		</div>
	{:else}
		<div class="card overflow-x-auto">
			<table class="table">
				<thead>
					<tr>
						<th>{t('tn.teams.col.team')}</th>
						<th class="hidden md:table-cell">{t('tn.teams.col.contact')}</th>
						<th class="text-right">{t('tn.teams.col.people')}</th>
						<th>{t('tn.teams.col.payment')}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each sorted as team (team.id)}
						<tr class="hover:bg-paper {team.withdrawn_at ? 'opacity-60' : ''}">
							<td>
								<div class="flex items-center gap-2">
									<TeamLogo name={team.name} logoUrl={team.logo_url} color={team.primary_color} />
									<a class="link font-semibold" href="{TBASE}/{tn.id}/teams/{team.id}">{team.name}</a>
									{#if team.is_home_team}<span class="badge badge-sm badge-info">{t('tn.teams.home')}</span>{/if}
									{#if team.is_foreign}<span class="badge badge-sm badge-outline">{t('tn.reg.foreign')}</span>{/if}
									{#if team.has_age_flag}<span class="badge badge-sm badge-warning">{t('tn.roster.age_flag')}</span>{/if}
									{#if team.withdrawn_at}<span class="badge badge-sm badge-error" title={team.withdrawal_reason}>{t('tn.teams.withdrawn')}</span>{/if}
								</div>
							</td>
							<td class="hidden text-xs md:table-cell">{team.contact_name}{team.contact_email ? ` · ${team.contact_email}` : ''}</td>
							<td class="text-right font-mono text-xs">{team.player_count} + {team.staff_count}</td>
							<td>
								{#if canManage && !team.withdrawn_at}
									<select class="select select-sm w-40" aria-label={t('tn.teams.payment_for', { name: team.name })} value={team.payment_status} disabled={run.busy} onchange={(e) => setPayment(team, e.currentTarget.value)}>
										{#each data.paymentStatuses as s (s)}<option value={s}>{t(`tn.pay.${s}`)}</option>{/each}
									</select>
								{:else}
									<span class="badge badge-sm badge-ghost">{t(`tn.pay.${team.payment_status}`)}</span>
								{/if}
							</td>
							<td class="text-right whitespace-nowrap">
								<a class="btn btn-outline btn-xs" href="{TBASE}/{tn.id}/teams/{team.id}">{t('tn.teams.roster')}</a>
								{#if canOperate}
									<label class="btn btn-outline btn-xs focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-club">
										{t('tn.teams.logo')}
										<input type="file" accept="image/*" class="sr-only" onchange={(e) => { uploadLogo(team, e.currentTarget.files?.[0]); e.currentTarget.value = ''; }} />
									</label>
								{/if}
								{#if canManage && !team.withdrawn_at}
									<button class="btn btn-outline btn-warning btn-xs" type="button" onclick={() => { withdrawing = team; withdrawReason = ''; run.clear(); withdrawOpen = true; }}>{t('tn.teams.withdraw')}</button>
								{/if}
								{#if canManage && !team.registration_id}
									<button class="btn btn-ghost btn-xs btn-square text-error" type="button" aria-label={t('tn.teams.delete_named', { name: team.name })} onclick={() => { deleting = team; run.clear(); deleteOpen = true; }}><Trash2 size={14} /></button>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<Modal bind:open={addOpen} title={t('tn.teams.add')}>
	{#snippet aside()}
		<div class="flex w-fit items-center gap-1" role="group" aria-label={t('tn.teams.kind')}>
			{#each ['club', 'foreign'] as k (k)}
				<button type="button" aria-pressed={kind === k} class="rounded-md px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {kind === k ? 'border border-ink bg-ink text-white' : 'border border-line text-muted hover:text-ink'}" onclick={() => (kind = k as 'club' | 'foreign')}>{t(`tn.teams.kind_${k}`)}</button>
			{/each}
		</div>
	{/snippet}
	<form class="grid gap-3" onsubmit={add}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		{#if kind === 'club'}
			<div class="form-control w-full">
				<label class="label" for="add-club-team"><span class="label-text">{t('tn.teams.club_team')}</span></label>
				<select id="add-club-team" class="select select-bordered w-full" bind:value={clubTeamId} required>
					<option value="" disabled>{t('tn.teams.pick')}</option>
					{#each data.clubTeams as ct (ct.id)}<option value={ct.id}>{ct.name}</option>{/each}
				</select>
				{#if run.fields.team_id}<span class="mt-1 block text-xs text-error">{run.fields.team_id}</span>{/if}
			</div>
		{:else}
			<Field label={t('tn.teams.f.name')} bind:value={foreign.foreign_name} error={run.fields.foreign_name} required />
			<Field label={t('tn.teams.f.contact_name')} bind:value={foreign.foreign_contact_name} error={run.fields.foreign_contact_name} required />
			<Field label={t('tn.teams.f.contact_email')} type="email" bind:value={foreign.foreign_contact_email} error={run.fields.foreign_contact_email} required />
			<Field label={t('tn.teams.f.contact_phone')} type="tel" bind:value={foreign.foreign_contact_phone} error={run.fields.foreign_contact_phone} />
			<div class="grid grid-cols-2 gap-3">
				<Field label={t('tn.teams.f.primary')} placeholder="#rrggbb" bind:value={foreign.foreign_primary_color} error={run.fields.foreign_primary_color} />
				<Field label={t('tn.teams.f.secondary')} placeholder="#rrggbb" bind:value={foreign.foreign_secondary_color} error={run.fields.foreign_secondary_color} />
			</div>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('tn.teams.add')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={withdrawOpen} title={t('tn.teams.withdraw_title', { name: withdrawing?.name ?? '' })}>
	<form class="grid gap-3" onsubmit={(e) => { e.preventDefault(); withdraw(); }}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<div class="form-control w-full">
			<label class="label" for="withdraw-reason"><span class="label-text">{t('tn.teams.withdraw_reason')}</span></label>
			<textarea id="withdraw-reason" class="textarea textarea-bordered min-h-20 w-full" bind:value={withdrawReason}></textarea>
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (withdrawOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-warning" type="submit" disabled={run.busy}>{t('tn.teams.withdraw')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('tn.teams.delete_title')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.teams.delete_body', { name: deleting?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={remove}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
<Toast message={run.notice} onhide={() => (run.notice = '')} />
