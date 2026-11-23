<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { Download, Eye, LoaderCircle, RefreshCw, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { checkSummary, rerunHref, type Check, type FixtureGames, type FixtureGamesDeleted, type FixturePreview, type FixtureSource, type FixtureSources, type Recheck, type RecheckAll } from '#lib/fixtures.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { formatDateTime } from '#lib/members/detail-helpers.ts';

	/**
	 * The team page's "Fixture import sources" card: the fixture lists remembered per season and competition, with a
	 * re-check (fetch + diff, never applied) and "forget" per row. `fixtures` is null when the list could not be loaded.
	 * Only `can_import` (the settings permission) gets the buttons; `canImportFallback` covers a failed load.
	 */
	let { teamId, seasonId, fixtures, canImportFallback }: { teamId: string; seasonId: string; fixtures: FixtureSources | null; canImportFallback: boolean } = $props();

	const canImport = $derived(fixtures ? fixtures.can_import : canImportFallback);
	let sources = $derived<FixtureSource[]>(fixtures?.sources ?? []);
	const featureOf = (code: string) => fixtures?.available.find((a) => a.source === code)?.feature_name ?? code;

	type RowState = { busy?: boolean; check?: Check; preview?: FixturePreview | null; error?: string };
	let rows = $state<Record<string, RowState>>({});
	let allBusy = $state(false);
	let allError = $state('');
	let skipped = $state<{ id: string; reason: string }[]>([]);

	const errorText = (res: { status: number; message?: string; fields: Record<string, string> }) => (res.status === 0 ? t('fixtures.network') : res.fields.season || res.message || t('fixtures.failed'));

	function applyResult(r: Recheck) {
		sources = sources.map((s) => (s.id === r.source.id ? r.source : s));
		rows[r.source.id] = { check: r.check, preview: r.preview, error: r.check.error || undefined };
	}

	async function recheck(s: FixtureSource) {
		rows[s.id] = { busy: true };
		const res = await api<Recheck>('POST', `/teams/${teamId}/fixture-sources/${s.id}/recheck`);
		if (res.ok && res.data) applyResult(res.data);
		else rows[s.id] = { error: errorText(res) };
	}

	async function recheckAll() {
		allBusy = true;
		allError = '';
		skipped = [];
		const res = await api<RecheckAll>('POST', `/teams/${teamId}/fixture-sources/recheck-all`, { query: { season: seasonId } });
		allBusy = false;
		if (!res.ok || !res.data) {
			allError = errorText(res);
			return;
		}
		for (const r of res.data.results) applyResult(r);
		skipped = res.data.skipped.map((s) => ({ id: s.id, reason: s.reason }));
		if (!res.data.results.length && !res.data.skipped.length) allError = t('fixtures.recheck_all_none');
	}

	function review(s: FixtureSource, preview: FixturePreview) {
		goto(rerunHref(teamId, s), { state: { fixturePreview: preview } });
	}

	// Forgetting a source: its games stay, or (opt-in) its upcoming games without recorded data are deleted too.
	let removing = $state<FixtureSource | null>(null);
	let removeOpen = $state(false);
	let removeBusy = $state(false);
	let removeError = $state('');
	let removeGames = $state<FixtureGames | null>(null);
	let alsoDelete = $state(false);
	let deletedNote = $state('');
	async function askRemove(s: FixtureSource) {
		removing = s;
		removeError = '';
		removeGames = null;
		alsoDelete = false;
		removeOpen = true;
		const res = await api<FixtureGames>('GET', `/teams/${teamId}/fixture-sources/${s.id}/games`);
		if (res.ok && res.data && removing?.id === s.id) removeGames = res.data;
	}
	async function remove() {
		if (!removing) return;
		removeBusy = true;
		removeError = '';
		const withGames = alsoDelete && !!removeGames?.deletable;
		const res = await api<FixtureGamesDeleted>('DELETE', `/teams/${teamId}/fixture-sources/${removing.id}`, { query: withGames ? { delete_games: true } : {} });
		removeBusy = false;
		if (!res.ok && res.status !== 404) {
			removeError = errorText(res);
			return;
		}
		const id = removing.id;
		sources = sources.filter((s) => s.id !== id);
		delete rows[id];
		removeOpen = false;
		if (res.data) {
			deletedNote = t('fixtures.games.deleted', { deleted: res.data.deleted, kept: res.data.kept_played + res.data.kept_with_data });
			await invalidateAll();
		}
	}

	const importHref = $derived(`/teams/${teamId}/import${seasonId ? `?season=${seasonId}` : ''}`);
</script>

<div class="card card-body gap-2">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h2 class="flex items-center gap-2 font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase"><Download size={18} aria-hidden="true" /> {t('teampage.fixtures.title')}</h2>
		{#if canImport}
			<div class="flex flex-wrap gap-2">
				{#if sources.length}
					<button class="btn btn-outline btn-sm gap-2" type="button" disabled={allBusy} onclick={recheckAll}>
						<RefreshCw size={14} aria-hidden="true" class={allBusy ? 'animate-spin' : ''} />
						{t('fixtures.recheck_all')}
					</button>
				{/if}
				<a class="btn btn-primary btn-sm gap-2" href={importHref}><Download size={14} aria-hidden="true" /> {t('fixtures.import')}</a>
			</div>
		{/if}
	</div>
	<p class="text-sm text-muted">{t('teampage.fixtures.help')}</p>
	{#if allError}<Alert kind="error">{allError}</Alert>{/if}
	{#if deletedNote}<Alert kind="success">{deletedNote}</Alert>{/if}
	{#if skipped.length}
		<Alert kind="warning" title={t('fixtures.skipped')}>
			{#each skipped as s (s.id)}<span class="block">{s.reason}</span>{/each}
		</Alert>
	{/if}

	{#if !fixtures}
		<p class="py-3 text-center text-sm text-muted">{t('fixtures.load_failed')}</p>
	{:else if !sources.length}
		<div class="rounded-lg border border-dashed border-line px-6 py-8 text-center">
			<p class="font-semibold text-ink">{t('fixtures.empty.title')}</p>
			<p class="mx-auto mt-1 max-w-xl text-sm text-muted">{t('fixtures.empty.body')}</p>
			{#if canImport}<a class="btn btn-outline btn-sm mt-3 gap-2" href={importHref}><Download size={14} aria-hidden="true" /> {t('fixtures.import')}</a>{/if}
		</div>
	{:else}
		<div class="overflow-x-auto">
			<table class="table">
				<thead>
					<tr><th>{t('teampage.fixtures.col.season')}</th><th>{t('teampage.fixtures.col.source')}</th><th>{t('teampage.fixtures.col.competition')}</th><th>{t('teampage.fixtures.col.url')}</th><th>{t('teampage.fixtures.col.imported')}</th><th>{t('teampage.fixtures.col.checked')}</th>{#if canImport}<th></th>{/if}</tr>
				</thead>
				<tbody>
					{#each sources as s (s.id)}
						{@const row = rows[s.id]}
						<tr>
							<td class="font-mono text-xs whitespace-nowrap">{s.season_name || '—'}</td>
							<td>
								<span class="font-semibold">{s.source}</span>
								{#if !s.enabled}<span class="mt-1 block w-fit rounded border border-warn-border bg-warn-bg px-1.5 text-xs text-warn-text" title={t('fixtures.needs_feature', { feature: featureOf(s.source) })}>{t('fixtures.not_enabled')}</span>{/if}
							</td>
							<td class="text-muted">{s.competition_label || '—'}</td>
							<td class="max-w-[16rem] truncate text-sm"><a class="link" href={s.url} target="_blank" rel="noreferrer" title={s.url}>{s.url}</a></td>
							<td class="font-mono text-xs whitespace-nowrap">{s.last_imported ? formatDateTime(s.last_imported) : '—'}</td>
							<td>
								<span class="font-mono text-xs whitespace-nowrap">{s.last_checked ? formatDateTime(s.last_checked) : '—'}</span>
								{#if s.last_check}<span class="block text-xs {s.last_check.error ? 'text-danger' : 'text-muted'}">{checkSummary(s.last_check)}</span>{/if}
							</td>
							{#if canImport}
								<td>
									<div class="flex items-center justify-end gap-1.5">
										<button class="btn btn-outline btn-xs gap-1" type="button" disabled={!s.enabled || row?.busy || allBusy} onclick={() => recheck(s)}>
											{#if row?.busy}<LoaderCircle size={13} class="animate-spin" aria-hidden="true" />{:else}<RefreshCw size={13} aria-hidden="true" />{/if}
											{t('teampage.fixtures.recheck')}
										</button>
										<button class="btn btn-outline btn-error btn-xs btn-square" type="button" title={t('fixtures.remove')} aria-label={t('fixtures.remove_named', { source: s.source, season: s.season_name })} onclick={() => askRemove(s)}><Trash2 size={13} /></button>
									</div>
								</td>
							{/if}
						</tr>
						{#if row && !row.busy && (row.error || row.check)}
							<tr class="bg-surface">
								<td colspan={canImport ? 7 : 6}>
									{#if row.error}
										<Alert kind="error" title={t('fixtures.recheck_failed')}>{row.error}</Alert>
									{:else if row.check?.has_changes}
										<div class="flex flex-wrap items-start justify-between gap-3">
											<div class="grid gap-1 text-sm">
												<span class="font-semibold text-ink">{t('fixtures.summary', { rescheduled: row.check.rescheduled.length, new: row.check.new_games.length, removed: row.check.removed_games.length })}</span>
												{#each row.check.rescheduled as g (g.game_id)}<span class="text-xs text-muted">{t('fixtures.check.rescheduled', { old: formatDateTime(g.old_start), new: formatDateTime(g.new_start) })}</span>{/each}
												{#each row.check.new_games as g (g.game_id)}<span class="text-xs text-muted">{t('fixtures.check.new', { start: formatDateTime(g.start), opponent: g.opponent_name })}</span>{/each}
												{#each row.check.removed_games as g (g.game_id)}<span class="text-xs text-muted">{t('fixtures.check.removed', { start: formatDateTime(g.start), title: g.title })}</span>{/each}
											</div>
											{#if row.preview}
												{@const preview = row.preview}
												<button class="btn btn-primary btn-sm gap-2" type="button" onclick={() => review(s, preview)}><Eye size={14} aria-hidden="true" /> {t('fixtures.review')}</button>
											{/if}
										</div>
									{:else}
										<p class="text-sm text-ok-text">{t('fixtures.up_to_date')}</p>
									{/if}
								</td>
							</tr>
						{/if}
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<Modal bind:open={removeOpen} title={t('fixtures.remove')}>
	{#if removeError}<div class="mb-3"><Alert kind="error">{removeError}</Alert></div>{/if}
	<p class="text-sm">{t('fixtures.remove_body', { source: removing?.source ?? '', season: removing?.season_name ?? '', label: removing?.competition_label || '—' })}</p>
	{#if removeGames}
		<div class="mt-3 grid gap-2 text-sm">
			{#if removeGames.deletable}
				<label class="flex items-start gap-2"><input type="checkbox" class="checkbox checkbox-sm mt-0.5" bind:checked={alsoDelete} /> <span>{t('fixtures.games.delete', { n: removeGames.deletable })}</span></label>
			{:else}
				<p class="text-muted">{t('fixtures.games.none')}</p>
			{/if}
			{#if removeGames.played || removeGames.with_data}<p class="text-muted">{t('fixtures.games.keep', { played: removeGames.played, data: removeGames.with_data })}</p>{/if}
			{#if alsoDelete}<Alert kind="warning">{t('fixtures.games.confirm', { n: removeGames.deletable })}</Alert>{/if}
		</div>
	{/if}
	<div class="modal-action mt-4 flex justify-end gap-2">
		<button class="btn btn-outline" type="button" onclick={() => (removeOpen = false)}>{t('common.cancel')}</button>
		<button class="btn btn-error gap-2" type="button" disabled={removeBusy} onclick={remove}><Trash2 size={16} /> {alsoDelete && removeGames?.deletable ? t('fixtures.remove_games') : t('fixtures.remove')}</button>
	</div>
</Modal>
