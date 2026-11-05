<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { applyAction, enhance, type SubmitFunction } from '$app/forms';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { ArrowLeft, CalendarDays, Check, CircleCheck, Download, FileUp, MapPin, RefreshCw, Shield } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import LocationMapping, { type LocationChoice } from '#lib/components/fixtures/LocationMapping.svelte';
	import OpponentMapping, { type OpponentChoice } from '#lib/components/fixtures/OpponentMapping.svelte';
	import type { Change, FixtureImportResult, FixturePreview, GameAction, PlannedGame, PlayDay, PlayDayPreview, PlayDayResult } from '#lib/fixtures.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { formatDateTime } from '#lib/members/detail-helpers.ts';

	let { data } = $props();

	type Step = 'source' | 'review' | 'done' | 'jsonReview' | 'jsonDone';
	type Failure = { status: number; code: string; error: string; fields: Record<string, string> };
	const JSON_KIND = 'json';

	// -- step 1: what to fetch (prefilled from the URL: the team page's "Review changes" and "Import fixtures") --
	const initial = () => {
		const p = data.prefill;
		const usable = data.available.filter((a) => a.enabled);
		const kind = data.available.some((a) => a.source === p.source) ? p.source : (usable[0]?.source ?? JSON_KIND);
		const season = data.seasons.some((s) => s.id === p.season) ? p.season : (data.seasons.find((s) => s.is_current)?.id ?? '');
		return { kind, url: p.url, season, label: p.label };
	};
	const start = initial();
	let kind = $state<string>(start.kind);
	let url = $state(start.url);
	let seasonId = $state(start.season);
	let label = $state(start.label);

	let step = $state<Step>('source');
	let busy = $state(false);
	let failure = $state<Failure | null>(null);
	const fields = $derived(failure?.fields ?? {});
	const chosen = $derived(data.available.find((a) => a.source === kind));

	// -- step 2: the preview and the mapping choices (the token only ever lives here) --
	let preview = $state<FixturePreview | null>(null);
	let opponentChoices = $state<Record<string, OpponentChoice>>({});
	let locationChoices = $state<Record<string, LocationChoice>>({});
	/** Per-game location override: '' = as planned, NONE = no location, else a location id. */
	let gameLocations = $state<Record<string, string>>({});
	let overrideOpen = $state<Record<string, boolean>>({});
	let filter = $state<'all' | GameAction>('all');
	let result = $state<FixtureImportResult | null>(null);
	const NONE = '__none';
	const defaultCountry = $derived(data.countries.some((c) => c.code === 'BE') ? 'BE' : (data.countries[0]?.code ?? ''));

	function showPreview(p: FixturePreview) {
		preview = p;
		kind = p.source;
		url = p.url;
		seasonId = p.season_id ?? seasonId;
		label = p.competition_label;
		// Choices made before a re-run are kept for the spellings that are still there.
		const opp: Record<string, OpponentChoice> = {};
		for (const o of p.unknown_opponents) opp[o.source_name] = opponentChoices[o.source_name] ?? { mode: o.default === 'keep' && o.current ? 'keep' : 'create', name: o.source_name, opponentId: o.suggestions[0]?.id ?? '', filter: '' };
		opponentChoices = opp;
		const loc: Record<string, LocationChoice> = {};
		for (const l of p.unknown_locations) loc[l.source_text] = locationChoices[l.source_text] ?? { mode: 'none', locationId: l.suggestions[0]?.id ?? '', name: l.source_text, address: '', zip_code: '', city: '', country: defaultCountry };
		locationChoices = loc;
		const ids = new Set(p.games.map((g) => g.game_id));
		gameLocations = Object.fromEntries(Object.entries(gameLocations).filter(([id]) => ids.has(id)));
		filter = 'all';
		failure = null;
		step = 'review';
	}

	const changeCount = $derived(preview ? preview.counts.create + preview.counts.update + preview.counts.remove : 0);
	const shown = $derived((preview?.games ?? []).filter((g) => filter === 'all' || g.action === filter));
	const opponentOf = $derived(new Map((preview?.unknown_opponents ?? []).flatMap((o) => o.game_ids.map((id) => [id, o.source_name] as const))));
	const venueOf = $derived(new Map((preview?.unknown_locations ?? []).flatMap((l) => l.game_ids.map((id) => [id, l.source_text] as const))));
	const locationName = (id: string) => data.locations.find((l) => l.id === id)?.name ?? '—';
	const opponentName = (id: string) => data.opponents.find((o) => o.id === id)?.name ?? preview?.unknown_opponents.flatMap((o) => o.suggestions).find((s) => s.id === id)?.name ?? '—';
	const isExpired = $derived(!!fields.token);

	/** What the import will use for a game's opponent, given the choices. */
	function opponentCell(g: PlannedGame): { text: string; isNew: boolean } {
		const name = opponentOf.get(g.game_id);
		const c = name !== undefined ? opponentChoices[name] : undefined;
		if (c?.mode === 'create') return { text: c.name || name || '', isNew: true };
		if (c?.mode === 'existing') return { text: c.opponentId ? opponentName(c.opponentId) : '—', isNew: false };
		return { text: g.opponent?.name ?? g.opponent_name, isNew: !g.opponent };
	}
	/** What the import will use for a game's location, given the choices and the per-game override. */
	function locationCell(g: PlannedGame): { text: string; isNew: boolean } {
		const o = gameLocations[g.game_id];
		if (o === NONE) return { text: '—', isNew: false };
		if (o) return { text: locationName(o), isNew: false };
		const venue = venueOf.get(g.game_id);
		const c = venue !== undefined ? locationChoices[venue] : undefined;
		if (c?.mode === 'create') return { text: c.name || venue || '', isNew: true };
		if (c?.mode === 'existing') return { text: c.locationId ? locationName(c.locationId) : '—', isNew: false };
		return { text: g.location?.name ?? '—', isNew: false };
	}

	const choices = $derived.by(() => {
		const opponents: Record<string, unknown> = {};
		for (const [name, c] of Object.entries(opponentChoices)) {
			if (c.mode === 'create') opponents[name] = { create: true, name: c.name.trim() || name };
			else if (c.mode === 'existing' && c.opponentId) opponents[name] = { opponent_id: c.opponentId };
		}
		const locations: Record<string, unknown> = {};
		for (const [text, c] of Object.entries(locationChoices)) {
			if (c.mode === 'none') locations[text] = null;
			else if (c.mode === 'existing' && c.locationId) locations[text] = { location_id: c.locationId };
			else if (c.mode === 'create') locations[text] = { create: { name: c.name.trim(), address: c.address.trim(), zip_code: c.zip_code.trim(), city: c.city.trim(), country: c.country } };
		}
		const game_locations: Record<string, string | null> = {};
		for (const [id, v] of Object.entries(gameLocations)) if (v) game_locations[id] = v === NONE ? null : v;
		return JSON.stringify({ opponents, locations, game_locations });
	});
	const incomplete = $derived(Object.values(opponentChoices).some((c) => c.mode === 'existing' && !c.opponentId) || Object.values(locationChoices).some((c) => c.mode === 'existing' && !c.locationId));

	// -- the JSON play-day file --
	let jsonPreview = $state<PlayDayPreview | null>(null);
	let jsonPicks = $state<Record<string, string>>({});
	let acknowledge = $state(false);
	let jsonResult = $state<PlayDayResult | null>(null);
	let fileName = $state('');
	const jsonLocation = (day: PlayDay) => data.locations.find((l) => l.id === jsonPicks[day.game_id]) ?? null;
	/** Mirrors events.services.json_import.home_warning for the location picked in the page. */
	function homeWarning(day: PlayDay): string {
		const l = jsonLocation(day);
		if (day.hosted_by_us && !l?.is_home) return t('fixtures.json.warn_not_home');
		if (!day.hosted_by_us && l?.is_home) return t('fixtures.json.warn_home');
		return '';
	}
	const jsonWarnings = $derived((jsonPreview?.games ?? []).filter((d) => homeWarning(d)).length);
	const jsonPayload = $derived(JSON.stringify(Object.fromEntries(Object.entries(jsonPicks).map(([id, v]) => [id, v || null]))));

	// -- form handling: every action answers into the page state, nothing else is kept --
	function handle(d: Record<string, any> | undefined, ok: boolean) {
		if (!d) return;
		if (!ok) {
			failure = { status: d.status ?? 0, code: d.code ?? '', error: d.error ?? '', fields: d.fields ?? {} };
			if (d.step === 'source') step = 'source';
			return;
		}
		failure = null;
		if (d.step === 'review') showPreview(d.preview as FixturePreview);
		else if (d.step === 'done') ((result = d.result as FixtureImportResult), (step = 'done'));
		else if (d.step === 'jsonReview') {
			jsonPreview = d.jsonPreview as PlayDayPreview;
			jsonPicks = Object.fromEntries(jsonPreview.games.map((g) => [g.game_id, g.location?.id ?? '']));
			acknowledge = false;
			fileName = d.filename ?? fileName;
			step = 'jsonReview';
		} else if (d.step === 'jsonDone') ((jsonResult = d.jsonResult as PlayDayResult), (step = 'jsonDone'));
		window.scrollTo({ top: 0 });
	}
	const submit: SubmitFunction = () => {
		busy = true;
		return async ({ result: r }) => {
			busy = false;
			if (r.type === 'success' || r.type === 'failure') handle(r.data, r.type === 'success');
			else await applyAction(r);
		};
	};

	function restart() {
		step = 'source';
		preview = null;
		jsonPreview = null;
		failure = null;
	}

	let previewForm: HTMLFormElement | undefined = $state();
	let rerunForm: HTMLFormElement | undefined = $state();
	onMount(() => {
		// "Review changes" on the team page hands over the re-check's preview; without it (a new tab, a reload) the
		// preview is simply run again from the prefilled fields.
		const handed = page.state.fixturePreview;
		if (handed && handed.team.id === data.team.id) showPreview(handed);
		else if (data.prefill.run && url && kind !== JSON_KIND && chosen?.enabled) previewForm?.requestSubmit();
	});

	const STEPS = ['source', 'review', 'done'] as const;
	const stepIndex = $derived(step === 'source' ? 0 : step === 'review' || step === 'jsonReview' ? 1 : 2);
	const teamHref = $derived(`/teams/${data.team.id}`);
	const ACTION_PILL: Record<GameAction, string> = { create: 'badge-info', update: 'badge-warning', remove: 'badge-error', unchanged: 'badge-ghost' };
	const COUNT_TONE: Record<GameAction, string> = { create: 'text-ink', update: 'text-warn-text', remove: 'text-danger', unchanged: 'text-muted' };
	const changeLabel = (k: string) => {
		const m = t(`fixtures.change.${k}`);
		return m.startsWith('fixtures.change.') ? k : m;
	};
	const changeValue = (v: string | null) => (!v ? '—' : /^\d{4}-\d{2}-\d{2}T/.test(v) ? formatDateTime(v) : v);
	const changeList = (c: Record<string, Change>) => Object.entries(c);
	const forbidden = $derived(failure?.status === 403);
	const sourceFieldError = $derived(fields.url || fields.season_id || fields.source || fields.competition_label || fields.file);

	usePageHeader(() => ({ title: t('fixtures.wizard.title'), context: data.team.name, actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href={teamHref}><ArrowLeft size={16} /> {t('fixtures.back_team')}</a>
{/snippet}

{#snippet failureAlert()}
	{#if failure}
		{#if forbidden}
			<Alert kind="error" title={t('fixtures.forbidden')}>{failure.error}</Alert>
		{:else if failure.status === 0}
			<Alert kind="error" title={t('fixtures.failed')}>{t('fixtures.network')}</Alert>
		{:else if !sourceFieldError || step !== 'source'}
			<Alert kind="error" title={t('fixtures.failed')}>{failure.error === 'failed' ? t('fixtures.failed_body') : failure.error}</Alert>
		{/if}
	{/if}
{/snippet}

{#snippet tile(name: string, n: number, tone: string, active = false, onclick: (() => void) | null = null)}
	{#if onclick}
		<button type="button" class="bg-white p-4 text-left hover:bg-surface {active ? 'ring-2 ring-club ring-inset' : ''}" aria-pressed={active} {onclick}>
			<span class="block font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{name}</span>
			<span class="block font-display text-3xl font-extrabold {tone}">{n}</span>
		</button>
	{:else}
		<div class="bg-white p-4">
			<span class="block font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{name}</span>
			<span class="block font-display text-3xl font-extrabold {tone}">{n}</span>
		</div>
	{/if}
{/snippet}

<svelte:head><title>{pageTitle(t('fixtures.wizard.title'))}</title></svelte:head>

<ol class="mb-4 flex flex-wrap items-center gap-2 text-sm" aria-label={t('fixtures.steps')}>
	{#each STEPS as s, i (s)}
		{@const state = i < stepIndex ? 'done' : i === stepIndex ? 'current' : 'todo'}
		<li class="flex items-center gap-2 {state === 'current' ? 'font-bold text-ink' : state === 'done' ? 'font-semibold text-club-dark' : 'text-muted'}" aria-current={state === 'current' ? 'step' : undefined}>
			<span class="grid h-6 w-6 place-items-center rounded-full text-xs {state === 'current' ? 'bg-club text-club-content' : state === 'done' ? 'bg-club/15 text-club-dark' : 'bg-rule'}">
				{#if state === 'done'}<Check size={14} strokeWidth={3} aria-hidden="true" />{:else}{i + 1}{/if}
			</span>{t(`fixtures.step.${s}`)}
			{#if i < STEPS.length - 1}<span class="mx-1 h-px w-8 {i < stepIndex ? 'bg-club/40' : 'bg-line'}"></span>{/if}
		</li>
	{/each}
</ol>

<!-- The re-run of the current preview (after "preview expired"): the same source, URL, season and label. -->
{#if preview}
	<form method="POST" action="?/preview" class="hidden" bind:this={rerunForm} use:enhance={submit}>
		<input type="hidden" name="source" value={preview.source} />
		<input type="hidden" name="url" value={preview.url} />
		<input type="hidden" name="season_id" value={preview.season_id ?? ''} />
		<input type="hidden" name="competition_label" value={preview.competition_label} />
	</form>
{/if}

{#if step === 'source'}
	<div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
		<div class="card card-body gap-4">
			<h2 class="card-title"><Download size={18} /> {t('fixtures.source.title')}</h2>
			<p class="text-sm text-muted">{t('fixtures.source.intro')}</p>
			{@render failureAlert()}

			<fieldset class="grid gap-2 sm:grid-cols-2">
				<legend class="sr-only">{t('fixtures.source.choose')}</legend>
				{#each data.available as a (a.source)}
					<label class="flex gap-3 rounded-lg border p-3 {kind === a.source ? 'border-club bg-row-sel' : 'border-line'} {a.enabled ? 'cursor-pointer hover:bg-surface' : 'cursor-not-allowed opacity-70'}">
						<input type="radio" class="mt-1 size-4 accent-club" name="kind" value={a.source} bind:group={kind} disabled={!a.enabled} />
						<span class="min-w-0">
							<span class="block font-semibold text-ink">{a.name}</span>
							{#if a.enabled}
								<span class="block text-xs text-muted">{a.help}</span>
							{:else}
								<span class="mt-1 block text-xs text-warn-text">{t('fixtures.needs_feature', { feature: a.feature_name })}</span>
							{/if}
						</span>
					</label>
				{/each}
				<label class="flex cursor-pointer gap-3 rounded-lg border p-3 hover:bg-surface {kind === JSON_KIND ? 'border-club bg-row-sel' : 'border-line'}">
					<input type="radio" class="mt-1 size-4 accent-club" name="kind" value={JSON_KIND} bind:group={kind} />
					<span class="min-w-0">
						<span class="block font-semibold text-ink">{t('fixtures.json.option')}</span>
						<span class="block text-xs text-muted">{t('fixtures.json.option_help')}</span>
					</span>
				</label>
			</fieldset>

			{#if kind === JSON_KIND}
				<form method="POST" action="?/jsonPreview" enctype="multipart/form-data" class="grid gap-3" use:enhance={submit}>
					<div class="form-control w-full">
						<label class="label" for="fx-file"><span class="label-text">{t('fixtures.json.file')}</span></label>
						<input id="fx-file" name="file" type="file" accept=".json,application/json" class="file-input w-full {fields.file ? 'file-input-error' : ''}" required onchange={(e) => (fileName = e.currentTarget.files?.[0]?.name ?? '')} />
						{#if fields.file}<span class="mt-1 block text-xs text-error">{fields.file === 'required' ? t('fixtures.json.file_required') : fields.file}</span>{:else}<span class="mt-1 block text-xs text-muted">{t('fixtures.json.file_help')}</span>{/if}
					</div>
					<div class="flex justify-end">
						<button class="btn btn-primary gap-2" type="submit" disabled={busy}><FileUp size={16} /> {t('fixtures.json.check')}</button>
					</div>
				</form>
			{:else if chosen}
				<form method="POST" action="?/preview" class="grid gap-3" bind:this={previewForm} use:enhance={submit}>
					<input type="hidden" name="source" value={chosen.source} />
					<Field label={t('fixtures.f.url')} name="url" type="url" required bind:value={url} placeholder={chosen.example} error={fields.url} help={chosen.help} disabled={!chosen.enabled} />
					<div class="grid gap-3 sm:grid-cols-2">
						<div class="form-control w-full">
							<label class="label" for="fx-season"><span class="label-text">{t('fixtures.f.season')}</span></label>
							<select id="fx-season" name="season_id" class="select select-bordered w-full {fields.season_id ? 'select-error' : ''}" bind:value={seasonId}>
								{#each data.seasons as s (s.id)}<option value={s.id}>{s.name}{s.is_current ? ` (${t('members.list.current')})` : ''}</option>{/each}
							</select>
							{#if fields.season_id}<span class="mt-1 block text-xs text-error">{fields.season_id}</span>{/if}
						</div>
						<Field label={t('fixtures.f.label')} name="competition_label" bind:value={label} error={fields.competition_label} help={t('fixtures.f.label_help')} maxlength={100} />
					</div>
					{#if fields.source}<p class="text-xs text-error">{fields.source}</p>{/if}
					<div class="flex justify-end">
						<button class="btn btn-primary gap-2" type="submit" disabled={busy || !chosen.enabled}>
							<RefreshCw size={16} class={busy ? 'animate-spin' : ''} />
							{busy ? t('fixtures.checking') : t('fixtures.check')}
						</button>
					</div>
				</form>
			{/if}
		</div>

		<div class="card card-body gap-2">
			<h2 class="card-title text-base">{t('fixtures.how.title')}</h2>
			<ol class="grid list-inside list-decimal gap-1.5 text-sm text-slate">
				<li>{t('fixtures.how.1')}</li>
				<li>{t('fixtures.how.2')}</li>
				<li>{t('fixtures.how.3')}</li>
			</ol>
			<p class="text-xs text-muted">{t('fixtures.how.note')}</p>
		</div>
	</div>
{:else if step === 'review' && preview}
	<div class="grid gap-4">
		<div class="card card-body gap-1">
			<div class="flex flex-wrap items-baseline justify-between gap-2">
				<h2 class="card-title">{data.available.find((a) => a.source === preview?.source)?.name ?? preview.source}{#if preview.competition_label}<span class="font-normal text-muted">· {preview.competition_label}</span>{/if}</h2>
				<span class="font-mono text-xs text-muted">{preview.season_name}</span>
			</div>
			<a class="link truncate text-sm" href={preview.url} target="_blank" rel="noreferrer">{preview.url}</a>
			{#if preview.scraped_team_name}<p class="text-sm text-muted">{t('fixtures.review.found_as', { name: preview.scraped_team_name, team: preview.team.name })}</p>{/if}
		</div>

		<div class="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-4">
			{#each ['create', 'update', 'remove', 'unchanged'] as const as k (k)}
				{@render tile(t(`fixtures.count.${k}`), preview.counts[k], COUNT_TONE[k], filter === k, () => (filter = filter === k ? 'all' : k))}
			{/each}
		</div>

		{#each preview.warnings as w (w)}<Alert kind="warning">{w}</Alert>{/each}
		{#if isExpired}
			<Alert kind="warning" title={t('fixtures.expired.title')}>
				{t('fixtures.expired.body')}
				<button class="btn btn-outline btn-sm mt-2 gap-2" type="button" disabled={busy} onclick={() => rerunForm?.requestSubmit()}><RefreshCw size={14} /> {t('fixtures.expired.rerun')}</button>
			</Alert>
		{:else}
			{@render failureAlert()}
		{/if}

		<div class="card">
			<div class="flex flex-wrap items-center justify-between gap-2 px-6 py-4">
				<h2 class="font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase">{t('fixtures.review.games')} <span class="font-mono text-xs font-normal text-muted">· {shown.length}</span></h2>
				{#if filter !== 'all'}<button class="btn btn-ghost btn-xs" type="button" onclick={() => (filter = 'all')}>{t('fixtures.review.show_all')}</button>{/if}
			</div>
			<div class="overflow-x-auto {preview.games.length > 12 ? 'max-h-[60vh] overflow-y-auto' : ''}">
				<table class="table">
					<thead class="sticky top-0 z-[1] bg-subhead">
						<tr><th>{t('fixtures.th.kickoff')}</th><th>{t('fixtures.th.action')}</th><th>{t('fixtures.th.game')}</th><th>{t('fixtures.th.opponent')}</th><th>{t('fixtures.th.venue')}</th><th>{t('fixtures.th.location')}</th><th>{t('fixtures.th.score')}</th><th>{t('fixtures.th.changes')}</th></tr>
					</thead>
					<tbody>
						{#each shown as g (`${g.action}-${g.game_id}`)}
							{@const opp = opponentCell(g)}
							{@const loc = locationCell(g)}
							{@const editable = g.action === 'create' || g.action === 'update'}
							<tr class={g.action === 'remove' ? 'bg-danger-bg/40' : g.action === 'unchanged' ? 'text-muted' : ''}>
								<td class="font-mono text-xs whitespace-nowrap">{formatDateTime(g.kickoff)}</td>
								<td><span class="badge badge-sm {ACTION_PILL[g.action]}">{t(`fixtures.action.${g.action}`)}</span></td>
								<td class="text-sm whitespace-nowrap">
									{#if g.home || g.away}
										<span class={g.our_side === 'home' ? 'font-bold text-ink' : ''}>{g.home}</span>
										<span class="text-muted">–</span>
										<span class={g.our_side === 'away' ? 'font-bold text-ink' : ''}>{g.away}</span>
									{:else}
										{g.title || '—'}
									{/if}
									<span class="block text-xs text-muted">{g.our_side === 'home' ? t('fixtures.home') : t('fixtures.away')}</span>
								</td>
								<td class="text-sm">
									{opp.text}
									{#if opp.isNew && editable}<span class="badge badge-info badge-sm ml-1">{t('fixtures.new')}</span>{/if}
									{#if opp.text !== g.opponent_name && g.opponent_name}<span class="block text-xs text-muted">{g.opponent_name}</span>{/if}
								</td>
								<td class="max-w-[12rem] text-xs text-muted">{g.venue || '—'}</td>
								<td class="text-sm">
									<span class="inline-flex items-center gap-1">{#if loc.text !== '—'}<MapPin size={12} aria-hidden="true" />{/if}{loc.text}</span>
									{#if loc.isNew}<span class="badge badge-info badge-sm ml-1">{t('fixtures.new')}</span>{/if}
									{#if editable}
										{#if overrideOpen[g.game_id] || gameLocations[g.game_id]}
											<select class="select select-sm mt-1 block w-full min-w-40 {fields[`game_locations.${g.game_id}`] ? 'select-error' : ''}" aria-label={t('fixtures.override', { kickoff: formatDateTime(g.kickoff) })} bind:value={gameLocations[g.game_id]}>
												<option value="">{t('fixtures.override.planned')}</option>
												<option value={NONE}>{t('fixtures.map.no_location')}</option>
												{#each data.locations as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
											</select>
											{#if fields[`game_locations.${g.game_id}`]}<span class="block text-xs text-error">{fields[`game_locations.${g.game_id}`]}</span>{/if}
										{:else}
											<button class="link block text-xs text-muted" type="button" onclick={() => (overrideOpen[g.game_id] = true)}>{t('fixtures.override.change')}</button>
										{/if}
									{/if}
								</td>
								<td class="font-mono text-xs whitespace-nowrap">{g.score_for !== null && g.score_against !== null ? `${g.score_for}–${g.score_against}` : '—'}</td>
								<td class="text-xs">
									{#each changeList(g.changes) as [k, c] (k)}
										<div><span class="font-semibold text-ink">{changeLabel(k)}:</span> <span class="text-muted line-through">{changeValue(c.old)}</span> → <span class="text-ink">{changeValue(c.new)}</span></div>
									{:else}
										{#if g.action === 'remove'}<span class="text-danger">{t('fixtures.review.removed_note')}</span>{/if}
									{/each}
								</td>
							</tr>
						{:else}
							<tr><td colspan="8" class="py-6 text-center text-muted">{preview.games.length ? t('fixtures.review.none_shown') : t('fixtures.review.no_games')}</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>

		{#if preview.unknown_opponents.length}
			<div class="card">
				<div class="px-6 py-4">
					<h2 class="flex items-center gap-2 font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase"><Shield size={18} aria-hidden="true" /> {t('fixtures.map.opponents')} <span class="font-mono text-xs font-normal text-muted">· {preview.unknown_opponents.length}</span></h2>
					<p class="text-sm text-muted">{t('fixtures.map.opponents_help')}</p>
				</div>
				<div class="border-t border-line">
					{#each preview.unknown_opponents as o (o.source_name)}
						<OpponentMapping item={o} bind:choice={opponentChoices[o.source_name]} opponents={data.opponents} error={fields[`opponents.${o.source_name}`] || fields[`opponents.${o.source_name}.name`]} />
					{/each}
				</div>
			</div>
		{/if}

		{#if preview.unknown_locations.length}
			<div class="card">
				<div class="px-6 py-4">
					<h2 class="flex items-center gap-2 font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase"><MapPin size={18} aria-hidden="true" /> {t('fixtures.map.locations')} <span class="font-mono text-xs font-normal text-muted">· {preview.unknown_locations.length}</span></h2>
					<p class="text-sm text-muted">{t('fixtures.map.locations_help')}</p>
				</div>
				<div class="border-t border-line">
					{#each preview.unknown_locations as l (l.source_text)}
						<LocationMapping item={l} bind:choice={locationChoices[l.source_text]} locations={data.locations} countries={data.countries} errors={fields} />
					{/each}
				</div>
			</div>
		{/if}

		<div class="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-white/95 p-3 shadow-lg backdrop-blur">
			<p class="text-sm text-muted">
				{#if changeCount}
					{t('fixtures.bar.summary', { create: preview.counts.create, update: preview.counts.update, remove: preview.counts.remove })}
				{:else}
					{t('fixtures.bar.nothing')}
				{/if}
				{#if incomplete}<span class="block text-warn-text">{t('fixtures.bar.incomplete')}</span>{/if}
			</p>
			<div class="flex flex-wrap gap-2">
				<button class="btn btn-outline" type="button" onclick={restart}>{t('fixtures.start_over')}</button>
				<form method="POST" action="?/import" use:enhance={submit}>
					<input type="hidden" name="token" value={preview.token} />
					<input type="hidden" name="choices" value={choices} />
					<button class="btn btn-primary gap-2" type="submit" disabled={busy || incomplete || isExpired}>
						<Download size={16} />
						{changeCount ? t('fixtures.bar.import', { n: changeCount }) : t('fixtures.bar.remember')}
					</button>
				</form>
			</div>
		</div>
	</div>
{:else if step === 'done' && result}
	<div class="grid max-w-3xl gap-4">
		<Alert kind="success" title={t('fixtures.done.title')}>{t('fixtures.done.body')}</Alert>
		<div class="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-5">
			{@render tile(t('fixtures.done.created'), result.created, 'text-ink')}
			{@render tile(t('fixtures.done.updated'), result.updated, 'text-ink')}
			{@render tile(t('fixtures.done.deleted'), result.deleted, 'text-ink')}
			{@render tile(t('fixtures.done.opponents'), result.opponents_created, 'text-ink')}
			{@render tile(t('fixtures.done.locations'), result.locations_created, 'text-ink')}
		</div>
		<div class="flex flex-wrap gap-2">
			<a class="btn btn-primary gap-2" href={teamHref}><CircleCheck size={16} /> {t('fixtures.back_team')}</a>
			<a class="btn btn-outline gap-2" href="/calendar"><CalendarDays size={16} /> {t('fixtures.to_calendar')}</a>
			<button class="btn btn-ghost" type="button" onclick={restart}>{t('fixtures.import_more')}</button>
		</div>
	</div>
{:else if step === 'jsonReview' && jsonPreview}
	<div class="grid gap-4">
		<div class="card card-body gap-1">
			<h2 class="card-title">{jsonPreview.title || jsonPreview.source}</h2>
			<p class="text-sm text-muted">{[jsonPreview.source, jsonPreview.competition, fileName].filter(Boolean).join(' · ')}</p>
			<p class="text-xs text-muted">{t('fixtures.json.club_wide')}</p>
		</div>
		<div class="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line bg-line">
			{@render tile(t('fixtures.count.create'), jsonPreview.counts.create, COUNT_TONE.create)}
			{@render tile(t('fixtures.count.update'), jsonPreview.counts.update, COUNT_TONE.update)}
			{@render tile(t('fixtures.count.unchanged'), jsonPreview.counts.unchanged, COUNT_TONE.unchanged)}
		</div>
		{#if fields.token}
			<Alert kind="warning" title={t('fixtures.expired.title')}>
				{t('fixtures.json.expired')}
				<button class="btn btn-outline btn-sm mt-2" type="button" onclick={restart}>{t('fixtures.json.upload_again')}</button>
			</Alert>
		{:else}
			{@render failureAlert()}
		{/if}

		<div class="card">
			<div class="overflow-x-auto {jsonPreview.games.length > 12 ? 'max-h-[60vh] overflow-y-auto' : ''}">
				<table class="table">
					<thead class="sticky top-0 z-[1] bg-subhead">
						<tr><th>{t('fixtures.th.when')}</th><th>{t('fixtures.th.action')}</th><th>{t('fixtures.th.teams')}</th><th>{t('fixtures.th.city')}</th><th>{t('fixtures.th.host')}</th><th>{t('fixtures.th.location')}</th><th>{t('fixtures.th.changes')}</th></tr>
					</thead>
					<tbody>
						{#each jsonPreview.games as d (d.game_id)}
							{@const warning = homeWarning(d)}
							{@const err = fields[`locations.${d.game_id}`]}
							<tr class={warning ? 'bg-row-warn' : ''}>
								<td class="font-mono text-xs whitespace-nowrap">{formatDateTime(d.start)}{#if d.end}<span class="block text-muted">– {formatDateTime(d.end)}</span>{/if}</td>
								<td><span class="badge badge-sm {ACTION_PILL[d.action]}">{t(`fixtures.action.${d.action}`)}</span></td>
								<td class="text-sm">{d.teams.map((x) => x.name).join(', ') || '—'}{#if d.label}<span class="block text-xs text-muted">{d.label}</span>{/if}</td>
								<td class="text-sm">{d.city || '—'}</td>
								<td><span class="badge badge-sm {d.hosted_by_us ? 'badge-success' : 'badge-ghost'}">{d.hosted_by_us ? t('fixtures.json.we_host') : t('fixtures.json.they_host')}</span></td>
								<td class="min-w-48">
									<select class="select select-sm w-full {err ? 'select-error' : ''}" aria-label={t('fixtures.json.location_for', { when: formatDateTime(d.start) })} bind:value={jsonPicks[d.game_id]}>
										<option value="">{t('fixtures.map.no_location')}</option>
										{#each data.locations as l (l.id)}<option value={l.id}>{l.name}{l.is_home ? ` (${t('fixtures.json.home_location')})` : ''}</option>{/each}
									</select>
									{#if warning}<span class="mt-1 block text-xs text-warn-text">{warning}</span>{/if}
									{#if err && err !== warning}<span class="mt-1 block text-xs text-error">{err}</span>{/if}
								</td>
								<td class="text-xs">
									{#each changeList(d.changes) as [k, c] (k)}
										<div><span class="font-semibold text-ink">{k}:</span> <span class="text-muted line-through">{c.old || '—'}</span> → <span class="text-ink">{c.new || '—'}</span></div>
									{/each}
								</td>
							</tr>
						{:else}
							<tr><td colspan="7" class="py-6 text-center text-muted">{t('fixtures.json.nothing')}</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>

		<form method="POST" action="?/jsonImport" class="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-white/95 p-3 shadow-lg backdrop-blur" use:enhance={submit}>
			<input type="hidden" name="token" value={jsonPreview.token} />
			<input type="hidden" name="locations" value={jsonPayload} />
			{#if jsonWarnings}
				<label class="flex cursor-pointer items-center gap-2 text-sm text-warn-text">
					<input type="checkbox" name="acknowledge_warnings" class="checkbox checkbox-sm" bind:checked={acknowledge} />
					{t('fixtures.json.acknowledge', { n: jsonWarnings })}
				</label>
			{:else}
				<p class="text-sm text-muted">{t('fixtures.json.bar', { create: jsonPreview.counts.create, update: jsonPreview.counts.update })}</p>
			{/if}
			<div class="flex flex-wrap gap-2">
				<button class="btn btn-outline" type="button" onclick={restart}>{t('fixtures.start_over')}</button>
				<button class="btn btn-primary gap-2" type="submit" disabled={busy || !jsonPreview.games.length || (jsonWarnings > 0 && !acknowledge) || !!fields.token}>
					<FileUp size={16} />
					{t('fixtures.json.import', { n: jsonPreview.games.length })}
				</button>
			</div>
		</form>
	</div>
{:else if step === 'jsonDone' && jsonResult}
	<div class="grid max-w-2xl gap-4">
		<Alert kind="success" title={t('fixtures.done.title')}>{t('fixtures.json.done')}</Alert>
		<div class="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line">
			{@render tile(t('fixtures.done.created'), jsonResult.created, 'text-ink')}
			{@render tile(t('fixtures.done.updated'), jsonResult.updated, 'text-ink')}
		</div>
		<div class="flex flex-wrap gap-2">
			<a class="btn btn-primary gap-2" href={teamHref}><CircleCheck size={16} /> {t('fixtures.back_team')}</a>
			<a class="btn btn-outline gap-2" href="/calendar"><CalendarDays size={16} /> {t('fixtures.to_calendar')}</a>
			<button class="btn btn-ghost" type="button" onclick={restart}>{t('fixtures.import_more')}</button>
		</div>
	</div>
{/if}
