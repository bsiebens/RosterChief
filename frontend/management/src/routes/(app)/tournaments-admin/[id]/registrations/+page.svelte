<script lang="ts">
	import { Check, ChevronDown, ClipboardList, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDate, fmtDateTime, Runner, type Registration } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const run = new Runner();
	let expanded = $state<string | null>(null);
	let rejecting = $state<Registration | null>(null);
	let rejectOpen = $state(false);
	let reason = $state('');

	const badge = (s: string) => ({ pending: 'badge-warning', approved: 'badge-success', rejected: 'badge-error' })[s] ?? 'badge-ghost';
	const approve = (r: Registration) => run.post(`/tournaments/registrations/${r.id}/approve`, {});
	async function reject() {
		if (!rejecting) return;
		if (await run.post(`/tournaments/registrations/${rejecting.id}/reject`, { reason: reason.trim() })) rejectOpen = false;
	}

	usePageHeader(() => ({ title: tn.name, context: t('tn.tab.registrations') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.registrations'))}</title></svelte:head>

<div class="grid gap-4">
	{#if run.error && !rejectOpen}<Alert kind="error">{run.error}</Alert>{/if}
	{#if data.error}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if !data.rows.length}
		<div class="card card-body items-center py-10 text-center text-muted">
			<ClipboardList size={28} />
			<p>{t('tn.reg.empty')}</p>
		</div>
	{:else}
		<div class="grid gap-3">
			{#each data.rows as r (r.id)}
				<section class="card card-body gap-3">
					<div class="flex flex-wrap items-center gap-3">
						<span class="size-5 shrink-0 rounded-full border border-line" style:background={r.primary_color || undefined} aria-hidden="true"></span>
						<div class="min-w-0 flex-1">
							<div class="flex flex-wrap items-center gap-2">
								<strong class="font-display text-ink">{r.name}</strong>
								<span class="badge badge-sm {badge(r.status)}">{t(`tn.reg.status.${r.status}`)}</span>
								{#if r.is_foreign}<span class="badge badge-sm badge-outline">{t('tn.reg.foreign')}</span>{/if}
								{#if r.status === 'pending' && r.is_oldest_pending}<span class="badge badge-sm badge-info">{t('tn.reg.oldest')}</span>{/if}
							</div>
							<p class="text-xs text-muted">
								{r.contact_name ?? ''}{r.contact_email ? ` · ${r.contact_email}` : ''}{r.contact_phone ? ` · ${r.contact_phone}` : ''}
							</p>
							<p class="font-mono text-xs text-muted">{t('tn.reg.submitted', { when: fmtDateTime(r.submitted_at) })}{r.club_team ? ` · ${r.club_team}` : ''}</p>
							{#if r.status === 'rejected' && r.rejection_reason}<p class="text-sm text-error">{r.rejection_reason}</p>{/if}
						</div>
						<div class="flex flex-wrap items-center gap-2">
							<button class="btn btn-ghost btn-sm gap-1" type="button" aria-expanded={expanded === r.id} onclick={() => (expanded = expanded === r.id ? null : r.id)}>
								{t('tn.reg.players', { n: r.players.length })} <ChevronDown size={14} class={expanded === r.id ? 'rotate-180' : ''} />
							</button>
							{#if r.status === 'pending'}
								<button class="btn btn-primary btn-sm gap-1" type="button" disabled={run.busy} onclick={() => approve(r)}><Check size={14} /> {t('tn.reg.approve')}</button>
								<button class="btn btn-outline btn-error btn-sm gap-1" type="button" disabled={run.busy} onclick={() => { rejecting = r; reason = ''; run.clear(); rejectOpen = true; }}><X size={14} /> {t('tn.reg.reject')}</button>
							{:else if r.tournament_team_id}
								<a class="btn btn-outline btn-sm" href="/tournaments-admin/{tn.id}/teams/{r.tournament_team_id}">{t('tn.reg.open_team')}</a>
							{/if}
						</div>
					</div>
					{#if expanded === r.id}
						{#if r.players.length}
							<div class="overflow-x-auto">
								<table class="table table-sm">
									<thead><tr><th>#</th><th>{t('tn.roster.name')}</th><th>{t('tn.roster.role')}</th><th>{t('tn.roster.dob')}</th><th>{t('tn.roster.dietary')}</th></tr></thead>
									<tbody>
										{#each r.players as p (p.id)}
											<tr>
												<td class="font-mono text-xs">{p.jersey_number ?? '—'}</td>
												<td>{p.name}{#if p.age_flagged} <span class="badge badge-warning badge-xs">{t('tn.roster.age_flag')}</span>{/if}</td>
												<td>{t(`tn.role.${p.role}`)}{p.position_label ? ` · ${p.position_label}` : ''}</td>
												<td class="font-mono text-xs">{fmtDate(p.date_of_birth)}</td>
												<td class="text-xs">{p.dietary_restrictions || '—'}</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						{:else}
							<p class="text-sm text-muted">{t('tn.reg.no_players')}</p>
						{/if}
					{/if}
				</section>
			{/each}
		</div>
	{/if}
</div>

<Modal bind:open={rejectOpen} title={t('tn.reg.reject_title', { name: rejecting?.name ?? '' })}>
	<form class="grid gap-3" onsubmit={(e) => { e.preventDefault(); reject(); }}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<div class="form-control w-full">
			<label class="label" for="reject-reason"><span class="label-text">{t('tn.reg.reason')}</span></label>
			<textarea id="reject-reason" class="textarea textarea-bordered min-h-20 w-full" bind:value={reason}></textarea>
			<span class="label-text-alt mt-1 block">{t('tn.reg.reason_help')}</span>
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (rejectOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error" type="submit" disabled={run.busy}>{t('tn.reg.reject')}</button>
		</div>
	</form>
</Modal>
