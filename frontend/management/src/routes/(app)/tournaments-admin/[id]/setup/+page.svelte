<script lang="ts">
	import { ArrowDown, ArrowLeft, ArrowLeftRight, ArrowRight, ArrowUp, CalendarClock, Coffee, GripVertical, Layers, ListChecks, LayoutGrid, MapPin, Network, Pencil, Plus, Repeat, Shuffle, Swords, Trash2, Users, Wrench, X, GitFork } from '@lucide/svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Toast from '#lib/components/Toast.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, fmtDay, FORMATS, fromLocalInput, numOrNull, Runner, strOrNull, TBASE, TIEBREAKERS, toLocalInput, type BreakRule, type FieldDoc, type MatchDoc, type StageDoc } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const setup = $derived(data.setup);
	const canOperate = $derived(tn.can_operate);
	const base = $derived(`/tournaments/${tn.id}`);
	const run = new Runner();
	const dates = $derived(setup?.dates?.length ? setup.dates : tn.dates);
	/** The tournament's own opening hours for a date: what a new field is available by default. */
	const hoursOf = (d: string) => {
		const day = tn.days?.find((x) => x.date === d);
		return { start: day?.starts_at ?? '09:00', end: day?.ends_at ?? '18:00' };
	};
	/** One icon per stage format, shown on top of its card in the add stage dialog. */
	const FORMAT_ICONS = { single_elim: Network, double_elim: GitFork, round_robin: Repeat, groups: LayoutGrid, swiss: Shuffle, custom: Wrench } as const;
	const isKnockout = (s: StageDoc) => s.format === 'single_elim' || s.format === 'double_elim';

	// --- fields
	type Win = { date: string; start: string; end: string };
	let fieldOpen = $state(false);
	let editField = $state<FieldDoc | null>(null);
	let fieldName = $state('');
	let windows = $state<Win[]>([]);
	let delField = $state<FieldDoc | null>(null);
	let delFieldOpen = $state(false);
	function openField(f: FieldDoc | null) {
		editField = f;
		fieldName = f?.name ?? '';
		windows = f ? f.availability.map((w) => ({ ...w })) : dates.map((d) => ({ date: d, ...hoursOf(d) }));
		run.clear();
		fieldOpen = true;
	}
	async function saveField(e: SubmitEvent) {
		e.preventDefault();
		const body = { name: fieldName.trim(), availability: windows.filter((w) => w.date && w.start && w.end) };
		if (await (editField ? run.patch(`${base}/fields/${editField.id}`, body) : run.post(`${base}/fields`, body))) fieldOpen = false;
	}
	async function removeField() {
		if (delField && (await run.del(`${base}/fields/${delField.id}`))) delFieldOpen = false;
	}

	// --- stages
	type StageForm = { name: string; format: string; placement: boolean; rounds: string; num_groups: string; points_win: string; points_draw: string; points_loss: string; tiebreaker_1: string; tiebreaker_2: string };
	const blankStage = (): StageForm => ({ name: '', format: 'round_robin', placement: false, rounds: '', num_groups: '', points_win: '3', points_draw: '1', points_loss: '0', tiebreaker_1: 'goal_diff', tiebreaker_2: 'goals_for' });
	let stageOpen = $state(false);
	let editStage = $state<StageDoc | null>(null);
	let sf = $state<StageForm>(blankStage());
	let delStage = $state<StageDoc | null>(null);
	let delStageOpen = $state(false);
	function openStage(s: StageDoc | null) {
		editStage = s;
		sf = s
			? { name: s.name, format: s.format, placement: s.placement, rounds: s.rounds === null ? '' : String(s.rounds), num_groups: s.num_groups === null ? '' : String(s.num_groups), points_win: String(s.points_win), points_draw: String(s.points_draw), points_loss: String(s.points_loss), tiebreaker_1: s.tiebreaker_1, tiebreaker_2: s.tiebreaker_2 }
			: blankStage();
		run.clear();
		stageOpen = true;
	}
	async function saveStage(e: SubmitEvent) {
		e.preventDefault();
		const body = {
			name: sf.name.trim(),
			format: sf.format,
			placement: sf.format === 'single_elim' ? sf.placement : false,
			rounds: sf.format === 'swiss' ? numOrNull(sf.rounds) : null,
			num_groups: sf.format === 'groups' ? numOrNull(sf.num_groups) : null,
			points_win: Number(sf.points_win || 0),
			points_draw: Number(sf.points_draw || 0),
			points_loss: Number(sf.points_loss || 0),
			tiebreaker_1: sf.tiebreaker_1,
			tiebreaker_2: sf.tiebreaker_2
		};
		if (await (editStage ? run.patch(`${base}/stages/${editStage.id}`, body) : run.post(`${base}/stages`, body))) stageOpen = false;
	}
	// The visible order of the break rules: follows the server, changes at once while dragging or using the arrows, then is saved.
	let ruleList = $state<BreakRule[]>([]);
	$effect(() => {
		ruleList = [...(setup?.break_rules ?? [])];
	});
	let dragRuleId = $state<string | null>(null);
	let overRuleId = $state<string | null>(null);
	/** Rules are tried in list order, top first. Moving one renumbers the priorities to match, changing only the rules whose number differs. */
	async function moveRule(from: number, to: number) {
		if (from === to || to < 0 || to >= ruleList.length) return;
		const next = [...ruleList];
		next.splice(to, 0, next.splice(from, 1)[0]);
		ruleList = next;
		for (const [k, r] of next.entries()) {
			if (r.priority === k) continue;
			if (!(await run.patch(`${base}/break-rules/${r.id}`, { priority: k }, undefined, { reload: false }))) break;
		}
		await invalidateAll();
	}
	const dropRule = (targetId: string) => {
		if (dragRuleId && dragRuleId !== targetId) moveRule(ruleList.findIndex((r) => r.id === dragRuleId), ruleList.findIndex((r) => r.id === targetId));
		dragRuleId = overRuleId = null;
	};
	// The visible order of the stages: follows the server, changes at once while dragging or using the arrows, then is saved.
	let stageList = $state<StageDoc[]>([]);
	$effect(() => {
		stageList = [...(setup?.stages ?? [])];
	});
	let dragStageId = $state<string | null>(null);
	let overStageId = $state<string | null>(null);
	/** Stages are played in list order, first on the left. Moving one renumbers the sequence to match, changing only the stages whose number differs. */
	async function moveStage(from: number, to: number) {
		if (from === to || to < 0 || to >= stageList.length) return;
		const next = [...stageList];
		next.splice(to, 0, next.splice(from, 1)[0]);
		stageList = next;
		for (const [k, s] of next.entries()) {
			if (s.sequence === k) continue;
			if (!(await run.patch(`${base}/stages/${s.id}`, { sequence: k }, undefined, { reload: false }))) break;
		}
		await invalidateAll();
	}
	const dropStage = (targetId: string) => {
		if (dragStageId && dragStageId !== targetId) moveStage(stageList.findIndex((x) => x.id === dragStageId), stageList.findIndex((x) => x.id === targetId));
		dragStageId = overStageId = null;
	};

	// --- selected stage and its matches
	const selectedId = $derived(page.url.searchParams.get('stage'));
	const selected = $derived(stageList.find((x) => x.id === selectedId) ?? null);
	/** Opens a stage's matches panel, or closes it again when that stage is already the open one. */
	function toggleStage(id: string) {
		const u = new URL(page.url.href);
		if (id === selectedId) u.searchParams.delete('stage');
		else u.searchParams.set('stage', id);
		goto(u, { replace: true, reset: false });
	}
	function selectStage(id: string) {
		if (id === selectedId) return;
		const u = new URL(page.url.href);
		u.searchParams.set('stage', id);
		goto(u, { replace: true, reset: false });
	}
	type Section = { key: string; title: string; matches: MatchDoc[] };
	const hasGroups = $derived((selected?.groups.length ?? 0) > 0);
	const sections = $derived.by<Section[]>(() => {
		const rounds = data.schedule?.stages.find((x) => x.stage.id === selectedId)?.rounds ?? [];
		if (!hasGroups) return rounds.map((r) => ({ key: `r${r.round_number}`, title: r.label, matches: r.matches }));
		const byKey = new Map<string, Section>();
		for (const r of rounds)
			for (const m of r.matches) {
				const key = m.group ? `g${m.group.id}` : `r${r.round_number}`;
				if (!byKey.has(key)) byKey.set(key, { key, title: m.group ? m.group.name : r.label, matches: [] });
				byKey.get(key)!.matches.push(m);
			}
		// A stable order: groups by name, and each group's matches by round then position, so editing a match never moves it around.
		return [...byKey.values()]
			.sort((x, y) => x.title.localeCompare(y.title))
			.map((x) => ({ ...x, matches: [...x.matches].sort((p, q) => p.round_number - q.round_number || p.sequence - q.sequence) }));
	});
	const matchTotal = $derived(sections.reduce((n, x) => n + x.matches.length, 0));
	const locked = (m: MatchDoc) => m.status === 'completed' || m.status === 'complete';
	const matchLabel = (m: MatchDoc) => `${m.home_display} – ${m.away_display}`;
	const matchUrl = (id: string) => `/tournaments/matches/${id}`;

	let swapping = $state<string | null>(null);
	/** One click: home becomes away and away becomes home. */
	async function swap(m: MatchDoc) {
		swapping = m.id;
		await run.patch(`${matchUrl(m.id)}/teams`, { home_team_id: m.away_team?.id ?? null, away_team_id: m.home_team?.id ?? null });
		swapping = null;
	}

	let replaceOpen = $state(false);
	let replacing = $state<MatchDoc | null>(null);
	let repHome = $state('');
	let repAway = $state('');
	function openReplace(m: MatchDoc) {
		replacing = m;
		repHome = m.home_team?.id ?? '';
		repAway = m.away_team?.id ?? '';
		run.clear();
		replaceOpen = true;
	}
	async function saveReplace(e: SubmitEvent) {
		e.preventDefault();
		if (replacing && (await run.patch(`${matchUrl(replacing.id)}/teams`, { home_team_id: strOrNull(repHome), away_team_id: strOrNull(repAway) }))) replaceOpen = false;
	}

	let moveOpen = $state(false);
	let moving = $state<MatchDoc | null>(null);
	let moveField = $state('');
	let moveStart = $state('');
	function openMove(m: MatchDoc) {
		moving = m;
		moveField = m.field?.id ?? setup?.fields[0]?.id ?? '';
		moveStart = toLocalInput(m.scheduled_start);
		run.clear();
		moveOpen = true;
	}
	async function saveMove(e: SubmitEvent) {
		e.preventDefault();
		if (moving && (await run.post(`${matchUrl(moving.id)}/reschedule`, { field_id: moveField, scheduled_start: fromLocalInput(moveStart) }))) moveOpen = false;
	}

	let delMatchOpen = $state(false);
	let delMatch = $state<MatchDoc | null>(null);
	async function removeMatch() {
		if (delMatch && (await run.del(matchUrl(delMatch.id)))) delMatchOpen = false;
	}

	let addOpen = $state(false);
	let af = $state({ home: '', away: '', round: '1', label: '', group: '', field: '', start: '' });
	function openAdd() {
		const last = sections.at(-1)?.matches.at(-1);
		af = { home: '', away: '', round: String(last?.round_number ?? 1), label: '', group: '', field: '', start: '' };
		run.clear();
		addOpen = true;
	}
	async function saveAdd(e: SubmitEvent) {
		e.preventDefault();
		if (!selected) return;
		const body = {
			home_team_id: strOrNull(af.home),
			away_team_id: strOrNull(af.away),
			round_number: numOrNull(af.round),
			round_label: af.label.trim(),
			group_id: hasGroups ? strOrNull(af.group) : null,
			field_id: strOrNull(af.field),
			scheduled_start: af.field && af.start ? fromLocalInput(af.start) : null
		};
		if (await run.post(`${base}/stages/${selected.id}/matches`, body)) addOpen = false;
	}
	async function removeStage() {
		if (delStage && (await run.del(`${base}/stages/${delStage.id}`))) delStageOpen = false;
	}
	const doGenerate = (s: StageDoc, regenerate: boolean) =>
		run.post<{ generated_count: number }>(`${base}/stages/${s.id}/generate-matches`, regenerate ? { regenerate: true } : {}, (d) => void (run.notice = t('tn.setup.generated', { n: d?.generated_count ?? 0 })), { notice: '' });
	// Regenerating throws the stage's matches (and any changes made to them) away, so it asks first.
	let regenStage = $state<StageDoc | null>(null);
	let regenOpen = $state(false);
	const generate = (s: StageDoc) => {
		if (!s.match_count) return doGenerate(s, false);
		regenStage = s;
		run.clear();
		regenOpen = true;
	};
	async function confirmRegenerate() {
		if (regenStage && (await doGenerate(regenStage, true))) regenOpen = false;
	}

	// --- break rules
	type RuleForm = { field_id: string; trigger_type: string; every_games: string; every_minutes: string; after_round: string; win_start: string; win_end: string; gap_minutes: string; replaces_default_gap: boolean };
	const blankRule = (): RuleForm => ({ field_id: '', trigger_type: 'game_count', every_games: '', every_minutes: '', after_round: '', win_start: '', win_end: '', gap_minutes: '', replaces_default_gap: false });
	let ruleOpen = $state(false);
	let editRule = $state<BreakRule | null>(null);
	let rf = $state<RuleForm>(blankRule());
	let delRule = $state<BreakRule | null>(null);
	let delRuleOpen = $state(false);
	const s = (v: unknown) => (v === null || v === undefined ? '' : String(v));
	function openRule(r: BreakRule | null) {
		editRule = r;
		rf = r
			? { field_id: r.field_id ?? '', trigger_type: r.trigger_type, every_games: s(r.trigger_every_n_games), every_minutes: s(r.trigger_every_n_minutes), after_round: s(r.trigger_after_round_number), win_start: r.trigger_window_start ?? '', win_end: r.trigger_window_end ?? '', gap_minutes: s(r.gap_minutes), replaces_default_gap: r.replaces_default_gap }
			: blankRule();
		run.clear();
		ruleOpen = true;
	}
	async function saveRule(e: SubmitEvent) {
		e.preventDefault();
		const body = {
			field_id: strOrNull(rf.field_id),
			trigger_type: rf.trigger_type,
			trigger_every_n_games: rf.trigger_type === 'game_count' ? numOrNull(rf.every_games) : null,
			trigger_every_n_minutes: rf.trigger_type === 'elapsed_time' ? numOrNull(rf.every_minutes) : null,
			trigger_after_round_number: rf.trigger_type === 'round_boundary' ? numOrNull(rf.after_round) : null,
			trigger_window_start: rf.trigger_type === 'clock_window' ? strOrNull(rf.win_start) : null,
			trigger_window_end: rf.trigger_type === 'clock_window' ? strOrNull(rf.win_end) : null,
			gap_minutes: numOrNull(rf.gap_minutes),
			replaces_default_gap: rf.replaces_default_gap,
			// A new rule goes last; the order of the rules is then set with the arrows in the list.
			...(editRule ? {} : { priority: setup?.break_rules?.length ? Math.max(...setup.break_rules.map((r) => r.priority ?? 0)) + 1 : 0 })
		};
		if (await (editRule ? run.patch(`${base}/break-rules/${editRule.id}`, body) : run.post(`${base}/break-rules`, body))) ruleOpen = false;
	}
	async function removeRule() {
		if (delRule && (await run.del(`${base}/break-rules/${delRule.id}`))) delRuleOpen = false;
	}
	const fieldName_ = (id: string | null) => setup?.fields.find((f) => f.id === id)?.name ?? t('tn.setup.all_fields');

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.tab.setup') }));
</script>

<svelte:head><title>{pageTitle(t('tn.tab.setup'))}</title></svelte:head>

{#snippet err()}
	{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
{/snippet}

<div class="grid gap-5">
	{#if data.error || !setup}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#if run.error && !fieldOpen && !stageOpen && !ruleOpen && !delFieldOpen && !delStageOpen && !regenOpen && !delRuleOpen && !replaceOpen && !moveOpen && !delMatchOpen && !addOpen}<Alert kind="error">{run.error}</Alert>{/if}

		<section class="card card-body card-hl gap-3">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><MapPin size={16} class="shrink-0" aria-hidden="true" /> {t('tn.setup.fields')}</h2>
				{#if canOperate}<button class="btn btn-outline btn-sm gap-1" type="button" onclick={() => openField(null)}><Plus size={14} /> {t('tn.setup.add_field')}</button>{/if}
			</div>
			{#each setup.fields as f (f.id)}
				<div class="flex flex-wrap items-start gap-3 rounded-lg border border-line p-3">
					<div class="min-w-0 flex-1">
						<strong class="text-ink">{f.name}</strong>
						<ul class="mt-1 grid gap-0.5 font-mono text-xs text-muted">
							{#each f.availability as w, i (i)}<li>{fmtDay(w.date)} · {w.start}–{w.end}</li>{:else}<li>{t('tn.setup.no_windows')}</li>{/each}
						</ul>
					</div>
					{#if canOperate}
						<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.setup.edit_named', { name: f.name })} onclick={() => openField(f)}><Pencil size={13} /></button>
						<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.setup.delete_named', { name: f.name })} onclick={() => { delField = f; run.clear(); delFieldOpen = true; }}><Trash2 size={13} /></button>
					{/if}
				</div>
			{:else}
				<p class="text-sm text-muted">{t('tn.setup.no_fields')}</p>
			{/each}
		</section>

		<section class="card card-body card-hl gap-3">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Layers size={16} class="shrink-0" aria-hidden="true" /> {t('tn.setup.stages')}</h2>
				{#if canOperate}<button class="btn btn-outline btn-sm gap-1" type="button" onclick={() => openStage(null)}><Plus size={14} /> {t('tn.setup.add_stage')}</button>{/if}
			</div>
			{#if !stageList.length}
				<p class="text-sm text-muted">{t('tn.setup.no_stages')}</p>
			{:else}
				<p class="text-xs text-muted">{t('tn.setup.flow_hint')}</p>
				<div class="grid items-start gap-4 {selected ? 'xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]' : ''}">
					<div class="flex flex-col gap-y-2 md:flex-row md:flex-wrap md:gap-y-3">
						{#each stageList as s, i (s.id)}
							{@const Icon = FORMAT_ICONS[s.format]}
							{@const isSel = selectedId === s.id}
							{@const canGenerate = canOperate && (isKnockout(s) || s.needs_seed_choice || s.format !== 'custom')}
							<div class="flex flex-col items-stretch md:flex-row md:items-start">
								<!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
								<div
									class="flex w-full flex-col gap-2 rounded-lg border p-3 transition-colors md:w-72 {isSel ? 'border-ink bg-row-sel' : 'border-line bg-white hover:bg-surface'} {overStageId === s.id && dragStageId !== s.id ? 'outline outline-2 -outline-offset-2 outline-club' : ''} {dragStageId === s.id ? 'opacity-50' : ''}"
									draggable={canOperate}
									onclick={(e) => {
										if (!(e.target as HTMLElement).closest('button, a, label, input')) selectStage(s.id);
									}}
									ondragstart={(e) => {
										dragStageId = s.id;
										if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
									}}
									ondragover={(e) => {
										if (!dragStageId) return;
										e.preventDefault();
										overStageId = s.id;
									}}
									ondrop={(e) => {
										e.preventDefault();
										dropStage(s.id);
									}}
									ondragend={() => (dragStageId = overStageId = null)}>
									<div class="flex items-start gap-2">
										<span class="flex size-9 shrink-0 items-center justify-center rounded-md {isSel ? 'bg-ink text-white' : 'bg-subhead text-muted'}"><Icon size={18} aria-hidden="true" /></span>
										<button class="min-w-0 flex-1 cursor-pointer text-left" type="button" aria-pressed={isSel} onclick={() => selectStage(s.id)}>
											<strong class="block truncate text-ink"><span class="font-mono text-xs text-muted">{i + 1}.</span> {s.name}</strong>
											<span class="mt-1 flex flex-wrap items-center gap-1">
												<span class="badge badge-sm badge-outline">{t(`tn.format.${s.format}`)}</span>
												{#if s.placement}<span class="badge badge-sm badge-info">{t('tn.setup.full_ranking')}</span>{/if}
												<span class="badge badge-sm {s.status === 'active' ? 'badge-success' : s.status === 'complete' ? 'badge-neutral' : 'badge-ghost'}">{t(`tn.stage_status.${s.status}`)}</span>
											</span>
										</button>
										{#if canOperate}<GripVertical size={16} class="mt-1 shrink-0 cursor-grab text-muted" aria-hidden="true" />{/if}
									</div>
									<dl class="grid grid-cols-3 gap-x-3 gap-y-1 border-t border-line pt-2">
										<div class="min-w-0">
											<dt class="font-display text-[10px] font-bold tracking-[.08em] text-muted uppercase">{t('tn.setup.stat.matches')}</dt>
											<dd class="text-sm font-semibold text-ink">{s.match_count}</dd>
										</div>
										<div class="min-w-0">
											<dt class="font-display text-[10px] font-bold tracking-[.08em] text-muted uppercase">{t('tn.setup.stat.groups')}</dt>
											<dd class="text-sm font-semibold text-ink">{s.groups.length ? s.groups.map((g) => g.name.replace(/^Group\s+/i, '')).join(' · ') : '—'}</dd>
										</div>
										<div class="min-w-0">
											<dt class="font-display text-[10px] font-bold tracking-[.08em] text-muted uppercase" title={t('tn.setup.stat.points_help')}>{t('tn.setup.stat.points')}</dt>
											<dd class="text-sm font-semibold text-ink">{s.points_win} / {s.points_draw} / {s.points_loss}</dd>
										</div>
									</dl>
									<div class="grid grid-cols-2 gap-2">
										<button class="btn btn-outline btn-xs w-full gap-1 {canGenerate ? '' : 'col-span-2'}" type="button" aria-pressed={isSel} onclick={() => toggleStage(s.id)}>
											<ListChecks size={13} aria-hidden="true" />
											{isSel ? t('tn.setup.hide_matches') : t('tn.setup.view_matches', { n: s.match_count })}
										</button>
										{#if canGenerate}
											{#if isKnockout(s) || s.needs_seed_choice}
												<a class="btn btn-primary btn-xs w-full gap-1" href="{TBASE}/{tn.id}/stages/{s.id}/seed"><Swords size={13} /> {t('tn.setup.seed')}</a>
											{:else}
												<button class="btn btn-primary btn-xs w-full" type="button" disabled={run.busy} onclick={() => generate(s)}>{s.match_count ? t('tn.setup.regenerate') : t('tn.setup.generate')}</button>
											{/if}
										{/if}
									</div>
									{#if canOperate}
										<div class="flex items-center justify-between gap-1">
											<span class="flex items-center gap-0.5">
												<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === 0 || run.busy} aria-label={t('tn.setup.stage_left')} title={t('tn.setup.stage_left')} onclick={() => moveStage(i, i - 1)}><ArrowLeft size={14} /></button>
												<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === stageList.length - 1 || run.busy} aria-label={t('tn.setup.stage_right')} title={t('tn.setup.stage_right')} onclick={() => moveStage(i, i + 1)}><ArrowRight size={14} /></button>
											</span>
											<span class="flex items-center gap-1">
												<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.setup.edit_named', { name: s.name })} title={t('tn.setup.edit_named', { name: s.name })} onclick={() => openStage(s)}><Pencil size={13} /></button>
												<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.setup.delete_named', { name: s.name })} title={t('tn.setup.delete_named', { name: s.name })} onclick={() => { delStage = s; run.clear(); delStageOpen = true; }}><Trash2 size={13} /></button>
											</span>
										</div>
									{/if}
								</div>
								{#if i < stageList.length - 1}
									<div class="flex flex-col items-center justify-center gap-1.5 px-2 py-1 text-muted md:w-28">
										<ArrowRight size={20} class="rotate-90 md:rotate-0" aria-hidden="true" />
										{#if s.format === 'groups' && isKnockout(stageList[i + 1])}<span class="text-center text-[11px] leading-tight">{t('tn.setup.feeds_advance')}</span>{/if}
									</div>
									{#if canOperate && s.format === 'groups'}
										<a class="btn btn-outline btn-xs self-center whitespace-nowrap" href="{TBASE}/{tn.id}/stages/{s.id}/next-phase">{t('tn.setup.next_phase')}</a>
									{/if}
								{/if}
							</div>
						{/each}
					</div>

					<div class="grid gap-3 rounded-lg border border-line p-3 {selected ? '' : 'hidden'}">
						{#if selected}
							<div class="flex flex-wrap items-center justify-between gap-2">
								<h3 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('tn.setup.panel_title', { name: selected.name })}</h3>
								{#if canOperate}<button class="btn btn-outline btn-sm gap-1" type="button" onclick={openAdd}><Plus size={14} /> {t('tn.setup.add_match')}</button>{/if}
							</div>
							{#if !data.schedule}
								<Alert kind="error">{t('common.error.generic')}</Alert>
							{:else if !matchTotal}
								<p class="text-sm text-muted">{selected.format === 'custom' ? t('tn.setup.panel_empty_custom') : t('tn.setup.panel_empty')}</p>
							{:else}
								{#each sections as sec (sec.key)}
									<div class="grid gap-1">
										<h4 class="text-xs font-bold text-muted">{sec.title}</h4>
										<ul class="grid divide-y divide-line rounded-lg border border-line">
											{#each sec.matches as m (m.id)}
												{@const lock = locked(m)}
												<li class="flex flex-wrap items-center gap-x-3 gap-y-1 p-2 hover:bg-paper">
													<div class="min-w-0 flex-1">
														<p class="text-sm text-ink">{m.home_display} <span class="text-muted">–</span> {m.away_display}</p>
														<p class="font-mono text-xs text-muted">
															{#if hasGroups && m.group}{m.round_label || `R${m.round_number}`} · {/if}{m.scheduled_start ? fmtDateTime(m.scheduled_start) : t('tn.sched.unscheduled')}{m.field ? ` · ${m.field.name}` : ''}
														</p>
													</div>
													{#if lock && m.score_home !== null}
														<span class="badge badge-sm badge-neutral font-mono">{m.score_home}–{m.score_away}</span>
													{:else if m.status !== 'scheduled'}
														<!-- "scheduled" is every unplayed match's normal state, and would read as the opposite of "Not scheduled" (no time yet). -->
														<span class="badge badge-sm badge-ghost">{t(`tn.match_status.${m.status}`)}</span>
													{/if}
													{#if canOperate}
														{@const tip = lock ? t('tn.setup.match_locked') : ''}
														<div class="flex items-center gap-1" title={tip}>
															<button class="btn btn-outline btn-xs btn-square" type="button" disabled={lock || run.busy || (!m.home_team && !m.away_team)} aria-label={t('tn.setup.match_swap')} title={tip || t('tn.setup.match_swap')} onclick={() => swap(m)}><ArrowLeftRight size={13} /></button>
															<button class="btn btn-outline btn-xs btn-square" type="button" disabled={lock || run.busy} aria-label={t('tn.setup.match_replace')} title={tip || t('tn.setup.match_replace')} onclick={() => openReplace(m)}><Users size={13} /></button>
															<button class="btn btn-outline btn-xs btn-square" type="button" disabled={lock || run.busy} aria-label={t('tn.setup.match_move')} title={tip || t('tn.setup.match_move')} onclick={() => openMove(m)}><CalendarClock size={13} /></button>
															<button class="btn btn-outline btn-error btn-xs btn-square" type="button" disabled={lock || run.busy} aria-label={t('tn.setup.match_delete')} title={tip || t('tn.setup.match_delete')} onclick={() => { delMatch = m; run.clear(); delMatchOpen = true; }}><Trash2 size={13} /></button>
														</div>
													{/if}
												</li>
											{/each}
										</ul>
									</div>
								{/each}
							{/if}
						{/if}
					</div>
				</div>
			{/if}
		</section>

		<section class="card card-body card-hl gap-3">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Coffee size={16} class="shrink-0" aria-hidden="true" /> {t('tn.setup.breaks')}</h2>
				{#if canOperate}<button class="btn btn-outline btn-sm gap-1" type="button" onclick={() => openRule(null)}><Plus size={14} /> {t('tn.setup.add_rule')}</button>{/if}
			</div>
			<p class="text-xs text-muted">{t('tn.setup.breaks_help')}</p>
			{#if !ruleList.length}
				<p class="text-sm text-muted">{t('tn.setup.no_rules')}</p>
			{:else}
				{#if canOperate}<p class="text-xs text-muted">{t('tn.setup.rules_drag_hint')}</p>{/if}
				<div class="overflow-x-auto rounded-lg border border-line">
					<table class="table">
						<thead>
							<tr>
								{#if canOperate}<th class="w-24"><span class="sr-only">{t('tn.setup.col.move')}</span></th>{/if}
								<th>{t('tn.setup.col.rule')}</th>
								<th class="hidden md:table-cell">{t('tn.setup.col.field')}</th>
								<th class="hidden sm:table-cell">{t('tn.setup.col.gap')}</th>
								{#if canOperate}<th></th>{/if}
							</tr>
						</thead>
						<tbody>
							{#each ruleList as r, i (r.id)}
								<tr
									class="hover:bg-paper {overRuleId === r.id && dragRuleId !== r.id ? 'outline outline-2 -outline-offset-2 outline-club' : ''} {dragRuleId === r.id ? 'opacity-50' : ''}"
									draggable={canOperate}
									ondragstart={(e) => {
										dragRuleId = r.id;
										if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
									}}
									ondragover={(e) => {
										if (!dragRuleId) return;
										e.preventDefault();
										overRuleId = r.id;
									}}
									ondrop={(e) => {
										e.preventDefault();
										dropRule(r.id);
									}}
									ondragend={() => (dragRuleId = overRuleId = null)}>
									{#if canOperate}
										<td class="whitespace-nowrap">
											<span class="inline-flex items-center gap-0.5 text-muted">
												<GripVertical size={16} class="cursor-grab" aria-hidden="true" />
												<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === 0 || run.busy} aria-label={t('tn.setup.rule_up')} title={t('tn.setup.rule_up')} onclick={() => moveRule(i, i - 1)}><ArrowUp size={14} /></button>
												<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === ruleList.length - 1 || run.busy} aria-label={t('tn.setup.rule_down')} title={t('tn.setup.rule_down')} onclick={() => moveRule(i, i + 1)}><ArrowDown size={14} /></button>
											</span>
										</td>
									{/if}
									<td class="text-sm text-ink">{r.summary}</td>
									<td class="hidden text-sm md:table-cell">{fieldName_(r.field_id)}</td>
									<td class="hidden sm:table-cell">
										{#if r.gap_minutes}
											<span class="font-mono text-xs">{r.gap_minutes} min</span>
											<span class="badge badge-sm {r.replaces_default_gap ? 'badge-warning' : 'badge-ghost'} ml-1">{r.replaces_default_gap ? t('tn.setup.gap_replaces') : t('tn.setup.gap_adds')}</span>
										{:else}
											<span class="badge badge-sm badge-outline">{t('tn.setup.gap_blackout')}</span>
										{/if}
									</td>
									{#if canOperate}
										<td class="text-right whitespace-nowrap">
											<div class="flex items-center justify-end gap-1">
												<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.setup.edit_rule')} title={t('tn.setup.edit_rule')} onclick={() => openRule(r)}><Pencil size={13} /></button>
												<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.setup.delete_rule')} title={t('tn.setup.delete_rule')} onclick={() => { delRule = r; run.clear(); delRuleOpen = true; }}><Trash2 size={13} /></button>
											</div>
										</td>
									{/if}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{/if}
</div>

<Modal bind:open={fieldOpen} title={editField ? t('tn.setup.edit_field') : t('tn.setup.add_field')} wide>
	<form class="grid gap-3" onsubmit={saveField}>
		{@render err()}
		<Field label={t('tn.setup.field_name')} bind:value={fieldName} error={run.fields.name} required />
		<fieldset class="grid gap-2">
			<legend class="label"><span class="label-text">{t('tn.setup.windows')}</span></legend>
			{#each windows as w, i (i)}
				<div class="grid grid-cols-[1fr_6.5rem_6.5rem_auto] items-center gap-2">
					<select class="select select-bordered select-sm w-full" aria-label={t('tn.setup.window_date')} bind:value={w.date}>
						{#each dates as d (d)}<option value={d}>{fmtDay(d)}</option>{/each}
					</select>
					<input class="input input-bordered input-sm" type="time" aria-label={t('tn.setup.window_start')} bind:value={w.start} />
					<input class="input input-bordered input-sm" type="time" aria-label={t('tn.setup.window_end')} bind:value={w.end} />
					<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('tn.setup.remove_window')} onclick={() => (windows = windows.filter((_, j) => j !== i))}><X size={14} /></button>
				</div>
			{/each}
			{#if run.fields.availability}<span class="text-xs text-error">{run.fields.availability}</span>{/if}
			{#each Object.entries(run.fields).filter(([k]) => k.startsWith('availability.')) as [k, m] (k)}<span class="text-xs text-error">{m}</span>{/each}
			<button class="btn btn-outline btn-sm w-fit gap-1" type="button" onclick={() => { const d = dates.find((x) => !windows.some((w) => w.date === x)) ?? dates[0] ?? ''; windows = [...windows, { date: d, ...hoursOf(d) }]; }}><Plus size={14} /> {t('tn.setup.add_window')}</button>
		</fieldset>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (fieldOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={stageOpen} wide title={editStage ? t('tn.setup.edit_stage') : t('tn.setup.add_stage')}>
	<form class="grid gap-3" onsubmit={saveStage}>
		{@render err()}
		<Field label={t('tn.setup.stage_name')} bind:value={sf.name} error={run.fields.name} required />
		<fieldset class="grid gap-2">
			<legend class="label"><span class="label-text">{t('tn.setup.format')}</span></legend>
			<div class="grid grid-cols-2 gap-2 lg:grid-cols-3" role="radiogroup" aria-label={t('tn.setup.format')}>
				{#each FORMATS as f (f)}
					{@const Icon = FORMAT_ICONS[f]}
					<label class="flex cursor-pointer flex-col gap-1.5 rounded-lg border p-3 hover:bg-surface focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-club {sf.format === f ? 'border-ink bg-row-sel' : 'border-line'}">
						<input type="radio" class="sr-only" name="st-format" value={f} bind:group={sf.format} />
						<span class="flex size-9 items-center justify-center rounded-md {sf.format === f ? 'bg-ink text-white' : 'bg-subhead text-muted'}"><Icon size={18} aria-hidden="true" /></span>
						<span class="block font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{t(`tn.format.${f}`)}</span>
						<span class="block text-xs text-muted">{t(`tn.format.${f}.help`)}</span>
					</label>
				{/each}
			</div>
			{#if run.fields.format}<span class="text-xs text-error">{run.fields.format}</span>{/if}
		</fieldset>
		{#if sf.format === 'single_elim'}
			<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
				<input type="checkbox" class="checkbox mt-0.5" bind:checked={sf.placement} />
				<span class="min-w-0">
					<span class="block text-sm font-semibold text-ink">{t('tn.setup.placement_title')}</span>
					<span class="block text-xs text-muted">{t('tn.setup.placement_help')}</span>
				</span>
			</label>
		{/if}
		{#if sf.format === 'swiss'}<Field label={t('tn.setup.rounds')} type="number" min="1" bind:value={sf.rounds} error={run.fields.rounds} required />{/if}
		{#if sf.format === 'groups'}<Field label={t('tn.setup.num_groups')} type="number" min="1" bind:value={sf.num_groups} error={run.fields.num_groups} required />{/if}
		<div class="grid grid-cols-3 gap-3">
			<Field label={t('tn.setup.points_win')} type="number" bind:value={sf.points_win} error={run.fields.points_win} />
			<Field label={t('tn.setup.points_draw')} type="number" bind:value={sf.points_draw} error={run.fields.points_draw} />
			<Field label={t('tn.setup.points_loss')} type="number" bind:value={sf.points_loss} error={run.fields.points_loss} />
		</div>
		<div class="grid grid-cols-2 gap-3">
			{#each [['tiebreaker_1', t('tn.setup.tiebreaker_1')], ['tiebreaker_2', t('tn.setup.tiebreaker_2')]] as [key, label] (key)}
				<div class="form-control w-full">
					<label class="label" for="st-{key}"><span class="label-text">{label}</span></label>
					<select id="st-{key}" class="select select-bordered w-full" bind:value={sf[key as 'tiebreaker_1' | 'tiebreaker_2']}>
						{#each TIEBREAKERS as tb (tb)}<option value={tb}>{t(`tn.tiebreaker.${tb}`)}</option>{/each}
					</select>
				</div>
			{/each}
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (stageOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={ruleOpen} title={editRule ? t('tn.setup.edit_rule') : t('tn.setup.add_rule')}>
	<form class="grid gap-3" onsubmit={saveRule}>
		{@render err()}
		<div class="grid grid-cols-2 gap-3">
			<div class="form-control w-full">
				<label class="label" for="br-field"><span class="label-text">{t('tn.setup.rule_field')}</span></label>
				<select id="br-field" class="select select-bordered w-full" bind:value={rf.field_id}>
					<option value="">{t('tn.setup.all_fields')}</option>
					{#each setup?.fields ?? [] as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
				</select>
			</div>
			<div class="form-control w-full">
				<label class="label" for="br-type"><span class="label-text">{t('tn.setup.trigger')}</span></label>
				<select id="br-type" class="select select-bordered w-full" bind:value={rf.trigger_type}>
					{#each ['game_count', 'elapsed_time', 'round_boundary', 'clock_window'] as k (k)}<option value={k}>{t(`tn.trigger.${k}`)}</option>{/each}
				</select>
				{#if run.fields.trigger_type}<span class="mt-1 block text-xs text-error">{run.fields.trigger_type}</span>{/if}
			</div>
		</div>
		{#if rf.trigger_type === 'game_count'}<Field label={t('tn.setup.every_games')} type="number" min="1" bind:value={rf.every_games} error={run.fields.trigger_every_n_games} required />{/if}
		{#if rf.trigger_type === 'elapsed_time'}<Field label={t('tn.setup.every_minutes')} type="number" min="1" bind:value={rf.every_minutes} error={run.fields.trigger_every_n_minutes} required />{/if}
		{#if rf.trigger_type === 'round_boundary'}<Field label={t('tn.setup.after_round')} type="number" min="1" bind:value={rf.after_round} error={run.fields.trigger_after_round_number} help={t('tn.setup.after_round_help')} />{/if}
		{#if rf.trigger_type === 'clock_window'}
			<div class="grid grid-cols-2 gap-3">
				<Field label={t('tn.setup.win_start')} type="time" bind:value={rf.win_start} error={run.fields.trigger_window_start} required />
				<Field label={t('tn.setup.win_end')} type="time" bind:value={rf.win_end} error={run.fields.trigger_window_end} required />
			</div>
		{/if}
		<div class="grid grid-cols-2 gap-3">
			<Field label={t('tn.setup.gap_minutes')} type="number" min="0" bind:value={rf.gap_minutes} error={run.fields.gap_minutes} />
		</div>
		<label class="flex cursor-pointer items-center gap-3 rounded-lg border border-line p-3"><input type="checkbox" class="checkbox" bind:checked={rf.replaces_default_gap} /><span class="text-sm">{t('tn.setup.replaces_gap', { n: tn.default_gap_minutes })}</span></label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (ruleOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delFieldOpen} title={t('tn.setup.delete_field')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.setup.delete_body', { name: delField?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delFieldOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={removeField}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
<Modal bind:open={regenOpen} title={t('tn.setup.regenerate_title')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.setup.regenerate_body', { name: regenStage?.name ?? '', n: regenStage?.match_count ?? 0 })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (regenOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-warning gap-2" type="button" disabled={run.busy} onclick={confirmRegenerate}>{t('tn.setup.regenerate')}</button>
		</div>
	</div>
</Modal>
<Modal bind:open={delStageOpen} title={t('tn.setup.delete_stage')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.setup.delete_body', { name: delStage?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delStageOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={removeStage}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
<Modal bind:open={delRuleOpen} title={t('tn.setup.delete_rule')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{delRule?.summary}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delRuleOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={removeRule}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>
{#snippet teamSelect(id: string, text: string, value: string, set: (v: string) => void, errorMsg?: string)}
	<div class="form-control w-full">
		<label class="label" for={id}><span class="label-text">{text}</span></label>
		<select {id} class="select select-bordered w-full" {value} onchange={(e) => set(e.currentTarget.value)}>
			<option value="">{t('tn.setup.match_tbd')}</option>
			{#each data.teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
		</select>
		{#if errorMsg}<span class="mt-1 block text-xs text-error">{errorMsg}</span>{/if}
	</div>
{/snippet}

<Modal bind:open={replaceOpen} wide title={t('tn.setup.match_replace')}>
	<form class="grid gap-3" onsubmit={saveReplace}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		{#if replacing}<p class="text-sm font-semibold">{matchLabel(replacing)}</p>{/if}
		<div class="grid gap-3 sm:grid-cols-2">
			{@render teamSelect('rp-home', t('tn.setup.match_home'), repHome, (v) => (repHome = v), run.fields.home_team_id)}
			{@render teamSelect('rp-away', t('tn.setup.match_away'), repAway, (v) => (repAway = v), run.fields.away_team_id)}
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (replaceOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={moveOpen} wide title={t('tn.setup.match_move')}>
	<form class="grid gap-3" onsubmit={saveMove}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		{#if moving}<p class="text-sm font-semibold">{matchLabel(moving)}</p>{/if}
		<div class="form-control w-full">
			<label class="label" for="mv-field"><span class="label-text">{t('tn.sched.field')}</span></label>
			<select id="mv-field" class="select select-bordered w-full" bind:value={moveField} required>
				{#each setup?.fields ?? [] as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
			</select>
		</div>
		<Field label={t('tn.sched.start')} type="datetime-local" bind:value={moveStart} required />
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (moveOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={delMatchOpen} title={t('tn.setup.match_delete')}>
	<div class="grid gap-3">
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p>{t('tn.setup.match_delete_body', { name: delMatch ? matchLabel(delMatch) : '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (delMatchOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="button" disabled={run.busy} onclick={removeMatch}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</div>
</Modal>

<Modal bind:open={addOpen} wide title={t('tn.setup.add_match')}>
	<form class="grid gap-3" onsubmit={saveAdd}>
		{#if run.error && !Object.keys(run.fields).length}<Alert kind="error">{run.error}</Alert>{/if}
		<div class="grid gap-3 sm:grid-cols-2">
			{@render teamSelect('am-home', t('tn.setup.match_home'), af.home, (v) => (af.home = v), run.fields.home_team_id)}
			{@render teamSelect('am-away', t('tn.setup.match_away'), af.away, (v) => (af.away = v), run.fields.away_team_id)}
		</div>
		<div class="grid gap-3 sm:grid-cols-2">
			<Field label={t('tn.setup.match_round')} type="number" min="1" bind:value={af.round} error={run.fields.round_number} />
			<Field label={t('tn.setup.match_round_label')} bind:value={af.label} error={run.fields.round_label} />
		</div>
		{#if hasGroups}
			<div class="form-control w-full">
				<label class="label" for="am-group"><span class="label-text">{t('tn.setup.match_group')}</span></label>
				<select id="am-group" class="select select-bordered w-full" bind:value={af.group}>
					<option value="">{t('tn.setup.match_no_group')}</option>
					{#each selected?.groups ?? [] as g (g.id)}<option value={g.id}>{g.name}</option>{/each}
				</select>
				{#if run.fields.group_id}<span class="mt-1 block text-xs text-error">{run.fields.group_id}</span>{/if}
			</div>
		{/if}
		<div class="grid gap-3 sm:grid-cols-2">
			<div class="form-control w-full">
				<label class="label" for="am-field"><span class="label-text">{t('tn.sched.field')}</span></label>
				<select id="am-field" class="select select-bordered w-full" bind:value={af.field}>
					<option value="">{t('tn.setup.match_no_field')}</option>
					{#each setup?.fields ?? [] as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
				</select>
				{#if run.fields.field_id}<span class="mt-1 block text-xs text-error">{run.fields.field_id}</span>{/if}
			</div>
			<Field label={t('tn.sched.start')} type="datetime-local" bind:value={af.start} error={run.fields.scheduled_start} help={t('tn.setup.match_start_help')} />
		</div>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (addOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary" type="submit" disabled={run.busy}>{t('common.save')}</button>
		</div>
	</form>
</Modal>
<Toast message={run.notice} onhide={() => (run.notice = '')} />
