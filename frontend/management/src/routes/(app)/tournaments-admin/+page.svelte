<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ChevronRight, Plus, Trophy } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateRange, STATUSES, statusBadge, TBASE } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const canManage = $derived(!!data.me?.can_manage_tournaments);

	function filter(key: string, value: string) {
		const u = new URL(page.url.href);
		if (value) u.searchParams.set(key, value);
		else u.searchParams.delete(key);
		goto(u, { reset: false });
	}
	usePageHeader(() => ({ title: t('tn.title'), context: t('tn.count', { n: data.rows.length }), actions }));
</script>

{#snippet actions()}
	{#if canManage}<a class="btn btn-primary gap-2" href="{TBASE}/new"><Plus size={16} /> {t('tn.add')}</a>{/if}
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
						<th><span class="sr-only">{t('tn.col.actions')}</span></th>
					</tr>
				</thead>
				<tbody>
					{#each data.rows as r (r.id)}
						<tr class="cursor-pointer hover:bg-paper" onclick={(e) => { if (!(e.target as HTMLElement).closest('a, button')) goto(`${TBASE}/${r.id}`); }}>
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
							<td class="text-right whitespace-nowrap">
								<a class="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-rule hover:text-ink" href="{TBASE}/{r.id}" aria-label={t('tn.open_named', { name: r.name })} title={t('tn.open')}><ChevronRight size={18} /></a>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
