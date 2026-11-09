<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { ChevronDown, ChevronRight, FileText, Settings2, Whistle } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import AssignDialog from '#lib/components/referees/AssignDialog.svelte';
	import GameDrawer from '#lib/components/referees/GameDrawer.svelte';
	import GameTile from '#lib/components/referees/GameTile.svelte';
	import KpiTiles from '#lib/components/referees/KpiTiles.svelte';
	import PaymentFormDialog from '#lib/components/referees/PaymentFormDialog.svelte';
	import WorkloadChart from '#lib/components/referees/WorkloadChart.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { canManageSettings } from '#lib/permissions.ts';
	import { eur, fmtDay, fmtTime, gameLabel, NEEDS, RANGES, TABS, type Game, type RefKind } from '#lib/referees.ts';

	let { data } = $props();

	// While SvelteKit switches pages this one is briefly handed the other page's data: keep showing the last data that was
	// this page's own, so nothing reads a missing field while it is torn down.
	// svelte-ignore state_referenced_locally
	let last = data;
	const shown = $derived.by(() => {
		if ('summary' in data && 'list' in data) last = data;
		return last as typeof data;
	});

	const summary = $derived(shown.summary);
	const games = $derived(shown.list?.games ?? []);
	const counts = $derived(shown.list?.counts ?? {});
	const f = $derived(shown.filters);
	const me = $derived(data.me);

	function href(change: Partial<{ tab: string; range: string; need: string }>) {
		const next = { tab: shown.tab, range: f.range, need: f.need, ...change };
		const sp = new URLSearchParams();
		if (next.tab !== 'games') sp.set('tab', next.tab);
		if (next.tab === 'games') {
			if (next.range !== 'two_weeks') sp.set('range', next.range);
			if (next.need !== 'all') sp.set('need', next.need);
		}
		const s = sp.toString();
		return s ? `?${s}` : '?';
	}
	const go = (to: string) => (e: Event) => {
		e.preventDefault();
		goto(to);
	};
	let version = $state(0);
	const refresh = () => {
		version++;
		invalidateAll();
	};

	// Dialogs hold ids, not games, so they keep showing the latest data after a save.
	let drawerId = $state<string | null>(null);
	let drawerOpen = $state(false);
	let assignId = $state<string | null>(null);
	let assignKind = $state<RefKind>('referee');
	let assignPreselect = $state('');
	let assignSignupOnly = $state(false);
	let assignOpen = $state(false);
	let formId = $state<string | null>(null);
	let formKind = $state<RefKind>('referee');
	let formOpen = $state(false);
	const byId = (id: string | null) => games.find((g) => g.id === id) ?? null;
	const drawerGame = $derived(byId(drawerId));
	const assignGame = $derived(byId(assignId));
	const formGame = $derived(byId(formId));
	const openDrawer = (g: Game) => ((drawerId = g.id), (drawerOpen = true));
	const openAssign = (g: Game, kind: RefKind, memberId = '', signupOnly = false) => ((assignId = g.id), (assignKind = kind), (assignPreselect = memberId), (assignSignupOnly = signupOnly), (assignOpen = true));
	const openForm = (g: Game, kind: RefKind) => ((formId = g.id), (formKind = kind), (formOpen = true));

	const seasonTotal = $derived(games.reduce((s, g) => s + Number(g.total), 0));
	const nowIso = $derived(new Date().toISOString());
	const upcoming = $derived(games.filter((g) => g.start >= nowIso));
	const past = $derived(games.filter((g) => g.start < nowIso));
	// The past games start collapsed, the upcoming ones open.
	let open = $state<Record<string, boolean>>({ upcoming: true, past: false });
	const groups = $derived([
		{ key: 'upcoming', games: upcoming },
		{ key: 'past', games: past }
	]);

	const tabs = $derived(TABS.map((key) => ({ key, label: t(`referees.tab.${key}`) })));
	const people = (g: Game) => [...g.referees, ...g.officials].map((p) => p.name).join(', ');

	usePageHeader(() => ({ title: t('referees.title'), context: shown.summary?.season_name ?? '' }));
</script>

<svelte:head><title>{pageTitle(t('referees.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
	<div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5">
		{#if shown.tab === 'games'}
			<div class="flex flex-wrap items-center gap-1" role="group" aria-label={t('referees.range')}>
				{#each RANGES as r (r)}
					<a href={href({ range: r })} onclick={go(href({ range: r }))} aria-current={f.range === r ? 'true' : undefined} class="btn btn-sm {f.range === r ? 'btn-primary' : 'btn-outline'}">{t(`referees.range.${r}`)}</a>
				{/each}
			</div>
		{/if}
		<div class="flex-1"></div>
		<div class="flex w-fit items-center gap-1 rounded-full bg-primary p-1" role="group" aria-label={t('referees.tabs')}>
			{#each tabs as tab (tab.key)}
				<a href={href({ tab: tab.key })} onclick={go(href({ tab: tab.key }))} aria-current={shown.tab === tab.key ? 'true' : undefined} class="rounded-full px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {shown.tab === tab.key ? 'bg-primary-content text-primary' : 'text-primary-dim hover:text-primary-content'}">{tab.label}</a>
			{/each}
		</div>
		<InfoTip label={t('referees.info_label')} text={t('referees.info')} align="right" />
	</div>
	{#if shown.tab === 'games'}
		<div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5" role="group" aria-label={t('referees.need')}>
			{#each NEEDS as n (n)}
				<a href={href({ need: n })} onclick={go(href({ need: n }))} aria-current={f.need === n ? 'true' : undefined} class="badge gap-1.5 px-2.5 py-1 {f.need === n ? 'border-club bg-club/10 text-club-dark' : 'hover:border-stroke'}">{t(`referees.need.${n}`)}<span class="font-mono">{counts[n] ?? 0}</span></a>
			{/each}
		</div>
	{/if}

	<div class="grid gap-5 px-7 py-5">
		{#if shown.error || !summary}
			<Alert kind="error">{t('referees.load_failed')}</Alert>
		{:else}
			<KpiTiles {summary} />

			{#if shown.tab === 'games'}
				{#if !games.length}
					<div class="card card-body items-center py-10 text-center text-muted"><Whistle size={28} /><p>{t('referees.empty_games')}</p></div>
				{:else}
					<div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
						{#each games as game (game.id)}<GameTile {game} onopen={openDrawer} />{/each}
					</div>
				{/if}
			{:else if shown.tab === 'payments'}
				<p class="text-sm text-muted">{t('referees.payments.hint')}</p>
				{#if !games.length}
					<div class="card card-body items-center py-10 text-center text-muted"><FileText size={28} /><p>{t('referees.empty_payments')}</p></div>
				{:else}
					<div class="card overflow-x-auto">
						<table class="table w-full table-fixed">
							<colgroup><col class="w-[30%]" /><col /><col class="w-28" /><col class="w-80" /></colgroup>
							<thead>
								<tr><th class="px-4">{t('referees.payments.col.game')}</th><th class="px-4">{t('referees.payments.col.people')}</th><th class="px-4 text-right">{t('referees.payments.col.total')}</th><th class="px-4"></th></tr>
							</thead>
							{#each groups.filter((g) => g.games.length) as group (group.key)}
								<tbody>
									<tr>
										<th colspan="4" class="bg-subhead p-0 text-left">
											<button type="button" class="flex w-full items-center gap-2 px-4 py-2 text-left hover:bg-paper" aria-expanded={open[group.key]} onclick={() => (open[group.key] = !open[group.key])}>
												{#if open[group.key]}<ChevronDown size={16} aria-hidden="true" />{:else}<ChevronRight size={16} aria-hidden="true" />{/if}
												<span class="font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{t(`referees.payments.${group.key}`)}</span>
												<span class="rounded-full border border-muted bg-white px-2 py-0.5 font-mono text-xs font-semibold text-slate">{group.games.length}</span>
												<span class="ml-auto font-mono text-xs text-muted">{eur(group.games.reduce((n, g) => n + Number(g.total), 0))}</span>
											</button>
										</th>
									</tr>
									{#if open[group.key]}
									{#each group.games as game (game.id)}
										<tr class="hover:bg-surface {game.fees_pending ? 'bg-row-warn' : ''}">
											<td>
												<div class="font-semibold">{gameLabel(game)}</div>
												<div class="font-mono text-xs text-muted">{fmtDay(game.start)} {fmtTime(game.start)}</div>
											</td>
											<td class="max-w-xs text-sm">{people(game)}{#if game.fees_pending}<span class="badge badge-xs badge-info ml-1">{t('referees.status.fees')}</span>{/if}</td>
											<td class="text-right font-mono font-semibold">{eur(game.total)}</td>
											<td class="px-4"><div class="flex flex-wrap justify-end gap-1.5">
												<button class="btn btn-outline btn-xs" type="button" onclick={() => openDrawer(game)}><Settings2 size={12} aria-hidden="true" /> {t('referees.manage')}</button>
												{#each game.referees.length && game.officials.length ? ['referee', 'official'] : game.officials.length ? ['official'] : ['referee'] as k (k)}
													<button class="btn btn-outline btn-xs" type="button" onclick={() => openForm(game, k as RefKind)}><FileText size={12} aria-hidden="true" /> {t(`referees.kind_one.${k as RefKind}`)}</button>
												{/each}
											</div></td>
										</tr>
									{/each}
									{/if}
								</tbody>
							{/each}
							<tfoot>
								<tr class="bg-subhead">
									<td colspan="2" class="px-4 py-2 text-right font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('referees.payments.total')}</td>
									<td class="px-4 py-2 text-right font-mono text-sm font-semibold text-ink">{eur(seasonTotal)}</td>
									<td></td>
								</tr>
							</tfoot>
						</table>
					</div>
				{/if}
			{:else}
				<p class="text-sm text-muted">{t('referees.workload.hint')}</p>
				{#if !summary.workload.length}
					<div class="card card-body items-center py-10 text-center text-muted"><Whistle size={28} /><p>{t('referees.empty_workload')}</p></div>
				{:else}
					<div class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
						<div class="card card-body gap-2">
							<h2 class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('referees.workload.chart')}</h2>
							<WorkloadChart rows={summary.workload} />
						</div>
						<div class="card overflow-x-auto">
							<table class="table">
								<thead>
									<tr><th>{t('referees.workload.col.person')}</th><th>{t('referees.workload.col.kind')}</th><th class="text-right">{t('referees.workload.col.games')}</th><th class="text-right">{t('referees.workload.col.total')}</th></tr>
								</thead>
								<tbody>
									{#each summary.workload as w (w.key)}
										<tr class="hover:bg-surface">
											<td>{#if w.member_id}<a class="font-semibold hover:underline" href="/members/{w.member_id}">{w.name}</a>{:else}<span class="font-semibold">{w.name}</span> <span class="badge badge-xs badge-outline">{t('referees.external')}</span>{/if}</td>
											<td><span class="badge badge-sm badge-ghost">{t(`referees.kind_one.${w.kind}`)}</span></td>
											<td class="text-right font-mono">{w.games}</td>
											<td class="text-right font-mono">{eur(w.total)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</div>
				{/if}
			{/if}
		{/if}
	</div>
</div>

{#if summary}
	<GameDrawer bind:open={drawerOpen} game={drawerGame} types={summary.types} {version} onchanged={refresh} onassign={openAssign} onform={openForm} />
	<AssignDialog bind:open={assignOpen} game={assignGame} kind={assignKind} types={summary.types[assignKind] ?? []} preselect={assignPreselect} signupOnly={assignSignupOnly} onassigned={refresh} />
	<PaymentFormDialog bind:open={formOpen} game={formGame} bind:kind={formKind} canSettings={canManageSettings(me)} />
{/if}
