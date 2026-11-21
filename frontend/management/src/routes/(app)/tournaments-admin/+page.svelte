<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Plus, Trophy } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import TournamentFields from '#lib/components/tournaments/TournamentFields.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateRange, Runner, STATUSES, statusBadge, TBASE, type TournamentDetail } from '#lib/tournaments.svelte.ts';
	import { formOf, payload } from '#lib/tournaments-form.ts';

	let { data } = $props();
	const canManage = $derived(!!data.me?.can_manage_tournaments);
	const run = new Runner();
	let open = $state(false);
	let v = $state(formOf());

	function filter(key: string, value: string) {
		const u = new URL(page.url.href);
		if (value) u.searchParams.set(key, value);
		else u.searchParams.delete(key);
		goto(u, { reset: false });
	}
	async function create(e: SubmitEvent) {
		e.preventDefault();
		let id = '';
		const ok = await run.run(() => api<TournamentDetail>('POST', '/tournaments', { body: payload(v) }), (d) => void (id = d?.id ?? ''), { reload: false });
		if (ok && id) await goto(`${TBASE}/${id}`);
	}

	usePageHeader(() => ({ title: t('tn.title'), context: t('tn.count', { n: data.rows.length }), actions }));
</script>

{#snippet actions()}
	{#if canManage}<button class="btn btn-primary gap-2" type="button" onclick={() => { v = formOf(); run.clear(); open = true; }}><Plus size={16} /> {t('tn.add')}</button>{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('tn.title'))}</title></svelte:head>

<div class="grid gap-4">
	<div class="flex flex-wrap items-center gap-2">
		<select class="select select-sm w-44 max-w-full" aria-label={t('tn.filter.season')} value={data.season} onchange={(e) => filter('season', e.currentTarget.value)}>
			<option value="">{t('tn.filter.all_seasons')}</option>
			{#each data.seasons as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
		</select>
		<select class="select select-sm w-44 max-w-full" aria-label={t('tn.filter.status')} value={data.status} onchange={(e) => filter('status', e.currentTarget.value)}>
			<option value="">{t('tn.filter.all_statuses')}</option>
			{#each STATUSES as s (s)}<option value={s}>{t(`tn.status.${s}`)}</option>{/each}
		</select>
	</div>

	{#if data.error}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else if !data.rows.length}
		<div class="card card-body items-center py-10 text-center text-muted">
			<Trophy size={28} />
			<p>{canManage ? t('tn.empty_manage') : t('tn.empty')}</p>
		</div>
	{:else}
		<div class="card overflow-x-auto">
			<table class="table">
				<thead>
					<tr>
						<th>{t('tn.col.name')}</th>
						<th>{t('tn.col.dates')}</th>
						<th class="hidden md:table-cell">{t('tn.col.season')}</th>
						<th>{t('tn.col.status')}</th>
						<th class="text-right">{t('tn.col.teams')}</th>
						<th class="text-right">{t('tn.col.pending')}</th>
					</tr>
				</thead>
				<tbody>
					{#each data.rows as r (r.id)}
						<tr class="hover:bg-paper">
							<td><a class="link font-semibold" href="{TBASE}/{r.id}">{r.name}</a></td>
							<td class="font-mono text-xs whitespace-nowrap">{fmtDateRange(r.start_date, r.end_date)}</td>
							<td class="hidden md:table-cell">{r.season.name}</td>
							<td>
								<span class="badge badge-sm {statusBadge(r.status)}">{t(`tn.status.${r.status}`)}</span>
								{#if r.accepts_registrations}<span class="badge badge-sm badge-outline ml-1">{t('tn.registration_open')}</span>{/if}
							</td>
							<td class="text-right font-mono text-xs">{r.teams_in_count}{r.max_teams ? ` / ${r.max_teams}` : ''}</td>
							<td class="text-right font-mono text-xs">
								{#if r.pending_registration_count && canManage}<a class="badge badge-warning badge-sm" href="{TBASE}/{r.id}/registrations">{r.pending_registration_count}</a>{:else}{r.pending_registration_count || '—'}{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<Modal bind:open title={t('tn.add')} wide>
	<form class="grid gap-3" onsubmit={create}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<TournamentFields bind:v errors={run.fields} teams={data.teams} />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('tn.create')}</button>
		</div>
	</form>
</Modal>
