<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { ArrowLeft, ArrowRight, CircleCheck, Download, LoaderCircle, History, RefreshCw, Shirt, TriangleAlert } from '@lucide/svelte';
	import { invalidateAll } from '$app/navigation';
	import { api } from '#lib/management-api.ts';
	import Alert from '#lib/components/Alert.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDateTime } from '#lib/events/types.ts';

	let { data } = $props();

	type RunResult = { mode: 'check' | 'import'; sources: number; errors: number; created: number; updated: number; deleted: number; new: number; rescheduled: number; removed: number; lines: string[] };
	type Task = { id: string; state: string; done: boolean; ok: boolean; result: RunResult | null };
	type PastResult = { games: number; updated: number; failed: number; skipped: number; with_score: number };
	let runs = $state<Record<string, { busy?: boolean; mode?: 'check' | 'import' | 'past'; result?: RunResult; past?: PastResult; error?: string }>>({});
	const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

	/** Queues the background task for one team (check only, or import) and waits for it. */
	async function run(teamId: string, apply: boolean) {
		const mode = apply ? 'import' : 'check';
		runs[teamId] = { busy: true, mode };
		const start = await api<Task>('POST', `/teams/${teamId}/fixture-sources/run`, { body: { apply } });
		if (!start.ok || !start.data) {
			runs[teamId] = { error: start.status === 0 ? t('fixtures.network') : start.message || t('fixtures.failed') };
			return;
		}
		let task = start.data;
		for (let i = 0; i < 120 && !task.done; i++) {
			await sleep(1500);
			const res = await api<Task>('GET', `/fixture-imports/tasks/${task.id}`);
			if (res.ok && res.data) task = res.data;
		}
		if (task.ok && task.result) runs[teamId] = { mode, result: task.result };
		else runs[teamId] = { error: task.done ? t('calimport.run_failed') : t('calimport.run_slow') };
		await invalidateAll();
	}
	/** Fetches score and game sheet of every played game of one team (background task). */
	async function updatePast(teamId: string) {
		runs[teamId] = { busy: true, mode: 'past' };
		const start = await api<{ id: string; done: boolean; ok: boolean; state: string; result: PastResult | null }>('POST', `/teams/${teamId}/fixture-sources/update-past`);
		if (!start.ok || !start.data) {
			runs[teamId] = { error: start.status === 0 ? t('fixtures.network') : start.message || t('fixtures.failed') };
			return;
		}
		let task = start.data;
		for (let i = 0; i < 240 && !task.done; i++) {
			await sleep(1500);
			const res = await api<typeof task>('GET', `/fixture-imports/tasks/${task.id}`);
			if (res.ok && res.data) task = res.data;
		}
		if (task.ok && task.result) runs[teamId] = { mode: 'past', past: task.result };
		else runs[teamId] = { error: task.done ? t('calimport.run_failed') : t('calimport.run_slow') };
		await invalidateAll();
	}
	const updateAllPast = () => Promise.all(data.rows.map((r) => updatePast(r.team.id)));
	const withSources = $derived(data.rows.filter((r) => r.sources?.length));
	const anyBusy = $derived(Object.values(runs).some((r) => r.busy));
	// Each top button only spins for its own kind of run.
	const checkingAll = $derived(Object.values(runs).some((r) => r.busy && r.mode === 'check'));
	const updatingAll = $derived(Object.values(runs).some((r) => r.busy && r.mode === 'past'));
	/** Checks every team that has saved sources (one background task each, in parallel). */
	const checkAll = () => Promise.all(withSources.map((r) => run(r.team.id, false)));
	const summary = (r: RunResult) =>
		r.mode === 'import'
			? t('calimport.done_import', { created: r.created, updated: r.updated, deleted: r.deleted })
			: r.new || r.rescheduled || r.removed
				? t('calimport.done_check', { new: r.new, moved: r.rescheduled, removed: r.removed })
				: t('calimport.done_nothing');
	const nothingAvailable = $derived(data.rows.length > 0 && data.rows.every((r) => !r.anyEnabled));
	usePageHeader(() => ({ title: t('calimport.title'), context: t('calimport.context'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/calendar"><ArrowLeft size={16} /> {t('calimport.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('calimport.title'))}</title></svelte:head>

<div class="grid gap-4">
	<p class="max-w-4xl text-sm text-muted">{t('calimport.intro')}</p>
	{#if withSources.length}
		<div class="flex">
			<button class="btn btn-outline gap-2" type="button" disabled={anyBusy} onclick={checkAll}>
				{#if checkingAll}<LoaderCircle size={16} class="animate-spin" />{:else}<RefreshCw size={16} />{/if}
				{t('calimport.check_all')}
			</button>
			<button class="btn btn-outline ml-2 gap-2" type="button" disabled={anyBusy} onclick={updateAllPast}>
				{#if updatingAll}<LoaderCircle size={16} class="animate-spin" />{:else}<History size={16} />{/if}
				{t('calimport.update_all_past')}
			</button>
		</div>
	{/if}
	{#if nothingAvailable}<Alert kind="warning" title={t('calimport.none_title')}>{t('calimport.none_body')}</Alert>{/if}

	{#if data.rows.length}
		<ul class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
			{#each data.rows as { team, sources } (team.id)}
				{@const state = runs[team.id]}
				<li class="card card-body h-full gap-3">
					<a class="flex items-center gap-3 hover:text-club-dark" href="/teams/{team.id}/import">
						<span class="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-club/10 text-club-dark"><Shirt size={18} aria-hidden="true" /></span>
						<h2 class="min-w-0 flex-1 truncate font-display text-lg font-extrabold tracking-[.03em] text-ink uppercase">{team.name}</h2>
						<ArrowRight size={16} class="text-dim" aria-hidden="true" />
					</a>
					{#if sources === null}
						<p class="flex items-center gap-1.5 text-sm text-muted"><TriangleAlert size={14} aria-hidden="true" /> {t('calimport.unknown')}</p>
					{:else if sources.length}
						<ul class="grid gap-1 text-sm">
							{#each sources.slice(0, 3) as src (src.id)}
								<li class="flex items-start justify-between gap-2"><span class="min-w-0"><span class="block font-semibold text-ink">{src.source}</span>{#if src.competition_label}<span class="block truncate text-xs text-muted">{src.competition_label}</span>{/if}</span><span class="shrink-0 font-mono text-xs whitespace-nowrap text-muted">{src.last_imported ? formatDateTime(src.last_imported) : t('calimport.never')}</span></li>
							{/each}
							{#if sources.length > 3}<li class="text-xs text-muted">{t('calimport.more', { n: sources.length - 3 })}</li>{/if}
						</ul>
						<!-- Pinned to the bottom of the card: what the last run did, then the actions (one main, two secondary). -->
						<div class="mt-auto grid gap-3 pt-1">
							{#if state?.busy}<p class="text-xs text-muted" role="status">{t('calimport.running')}</p>{/if}
							{#if state?.error}<p class="text-xs text-error" role="alert">{state.error}</p>{/if}
							{#if state?.past}
								<div class="rounded-lg bg-ok-bg p-2 text-xs text-ok-text" role="status">
									<p class="flex items-center gap-1.5 font-semibold"><CircleCheck size={13} aria-hidden="true" /> {t('calimport.past_done', { updated: state.past.updated, games: state.past.games, scores: state.past.with_score })}</p>
									{#if state.past.failed || state.past.skipped}<p class="mt-1 text-danger">{t('calimport.past_problems', { failed: state.past.failed, skipped: state.past.skipped })}</p>{/if}
								</div>
							{/if}
							{#if state?.result}
								<div class="rounded-lg bg-ok-bg p-2 text-xs text-ok-text" role="status">
									<p class="flex items-center gap-1.5 font-semibold"><CircleCheck size={13} aria-hidden="true" /> {summary(state.result)}</p>
									{#if state.result.errors}<p class="mt-1 text-danger">{state.result.lines.filter((l) => !l.endsWith(': ok')).join(' · ')}</p>{/if}
									{#if state.result.mode === 'check' && (state.result.new || state.result.rescheduled || state.result.removed)}<a class="mt-1 inline-block font-semibold underline" href="/teams/{team.id}/import">{t('calimport.review')}</a>{/if}
								</div>
							{/if}
							<div class="grid gap-2">
								<a class="btn btn-primary btn-sm w-full gap-1.5" href="/teams/{team.id}/import"><Download size={14} /> {t('calimport.review_import')}</a>
								<div class="grid grid-cols-2 gap-2">
									<button class="btn btn-outline btn-sm gap-1.5 px-2" type="button" disabled={state?.busy} onclick={() => run(team.id, false)}>
										{#if state?.busy && state.mode === 'check'}<LoaderCircle size={14} class="animate-spin" />{:else}<RefreshCw size={14} />{/if}
										{t('calimport.check')}
									</button>
									<button class="btn btn-outline btn-sm gap-1.5 px-2" type="button" disabled={state?.busy} onclick={() => updatePast(team.id)} title={t('calimport.update_past_hint')}>
										{#if state?.busy && state.mode === 'past'}<LoaderCircle size={14} class="animate-spin" />{:else}<History size={14} />{/if}
										{t('calimport.update_past')}
									</button>
								</div>
							</div>
						</div>
					{:else}
						<p class="text-sm text-muted">{t('calimport.no_sources')}</p>
						<a class="mt-auto flex items-center gap-1 text-sm font-semibold text-club-dark" href="/teams/{team.id}/import"><Download size={14} aria-hidden="true" /> {t('calimport.setup')} <ArrowRight size={14} aria-hidden="true" /></a>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<div class="card card-body items-center py-10 text-center text-muted"><Shirt size={28} /><p>{t('calimport.no_teams')}</p></div>
	{/if}
</div>
