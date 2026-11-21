<script lang="ts">
	import { ArrowDown, ArrowUp, Coffee, Layers, LayoutGrid, MapPin, Network, Pencil, Plus, Repeat, Shuffle, Swords, Trash2, Wrench, X, GitFork } from '@lucide/svelte';
	import { invalidateAll } from '$app/navigation';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Toast from '#lib/components/Toast.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDay, FORMATS, numOrNull, Runner, strOrNull, TBASE, TIEBREAKERS, type BreakRule, type FieldDoc, type StageDoc } from '#lib/tournaments.svelte.ts';

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
	type StageForm = { name: string; format: string; sequence: string; rounds: string; num_groups: string; points_win: string; points_draw: string; points_loss: string; tiebreaker_1: string; tiebreaker_2: string };
	const blankStage = (): StageForm => ({ name: '', format: 'round_robin', sequence: '', rounds: '', num_groups: '', points_win: '3', points_draw: '1', points_loss: '0', tiebreaker_1: 'goal_diff', tiebreaker_2: 'goals_for' });
	let stageOpen = $state(false);
	let editStage = $state<StageDoc | null>(null);
	let sf = $state<StageForm>(blankStage());
	let delStage = $state<StageDoc | null>(null);
	let delStageOpen = $state(false);
	function openStage(s: StageDoc | null) {
		editStage = s;
		sf = s
			? { name: s.name, format: s.format, sequence: String(s.sequence), rounds: s.rounds === null ? '' : String(s.rounds), num_groups: s.num_groups === null ? '' : String(s.num_groups), points_win: String(s.points_win), points_draw: String(s.points_draw), points_loss: String(s.points_loss), tiebreaker_1: s.tiebreaker_1, tiebreaker_2: s.tiebreaker_2 }
			: blankStage();
		run.clear();
		stageOpen = true;
	}
	async function saveStage(e: SubmitEvent) {
		e.preventDefault();
		const body = {
			name: sf.name.trim(),
			format: sf.format,
			sequence: numOrNull(sf.sequence) ?? undefined,
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
	/** Rules are tried in list order, top first. Moving one renumbers the priorities to match, changing only the rules whose number differs. */
	async function moveRule(i: number, by: -1 | 1) {
		const list = [...(setup?.break_rules ?? [])];
		const j = i + by;
		if (j < 0 || j >= list.length) return;
		[list[i], list[j]] = [list[j], list[i]];
		for (const [k, r] of list.entries()) {
			if (r.priority === k) continue;
			if (!(await run.patch(`${base}/break-rules/${r.id}`, { priority: k }, undefined, { reload: false }))) break;
		}
		await invalidateAll();
	}
	async function removeStage() {
		if (delStage && (await run.del(`${base}/stages/${delStage.id}`))) delStageOpen = false;
	}
	let placement = $state<Record<string, boolean>>({});
	const generate = (s: StageDoc) => run.post<{ generated_count: number }>(`${base}/stages/${s.id}/generate-matches`, placement[s.id] ? { with_placement: true } : {}, (d) => void (run.notice = t('tn.setup.generated', { n: d?.generated_count ?? 0 })), { notice: '' });

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
		{#if run.error && !fieldOpen && !stageOpen && !ruleOpen && !delFieldOpen && !delStageOpen && !delRuleOpen}<Alert kind="error">{run.error}</Alert>{/if}

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
			{#each setup.stages as s (s.id)}
				<div class="flex flex-wrap items-start gap-3 rounded-lg border border-line p-3">
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-2">
							<strong class="text-ink">{s.sequence}. {s.name}</strong>
							<span class="badge badge-sm badge-outline">{t(`tn.format.${s.format}`)}</span>
							<span class="badge badge-sm badge-ghost">{s.status}</span>
						</div>
						<p class="font-mono text-xs text-muted">
							{t('tn.setup.matches', { n: s.match_count })}{s.groups.length ? ` · ${s.groups.map((g) => g.name).join(', ')}` : ''} · {t('tn.setup.points', { w: s.points_win, d: s.points_draw, l: s.points_loss })}
						</p>
					</div>
					{#if canOperate}
						<div class="flex flex-wrap items-center gap-2">
							{#if isKnockout(s) || s.needs_seed_choice}
								<a class="btn btn-primary btn-sm gap-1" href="{TBASE}/{tn.id}/stages/{s.id}/seed"><Swords size={14} /> {t('tn.setup.seed')}</a>
							{:else}
								{#if s.supports_placement}<label class="flex items-center gap-1 text-xs"><input type="checkbox" class="checkbox checkbox-xs" bind:checked={placement[s.id]} /> {t('tn.setup.placement')}</label>{/if}
								<button class="btn btn-primary btn-sm" type="button" disabled={run.busy} onclick={() => generate(s)}>{s.match_count ? t('tn.setup.regenerate') : t('tn.setup.generate')}</button>
							{/if}
							{#if s.format === 'groups'}<a class="btn btn-outline btn-sm" href="{TBASE}/{tn.id}/stages/{s.id}/next-phase">{t('tn.setup.next_phase')}</a>{/if}
							<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.setup.edit_named', { name: s.name })} onclick={() => openStage(s)}><Pencil size={13} /></button>
							<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.setup.delete_named', { name: s.name })} onclick={() => { delStage = s; run.clear(); delStageOpen = true; }}><Trash2 size={13} /></button>
						</div>
					{/if}
				</div>
			{:else}
				<p class="text-sm text-muted">{t('tn.setup.no_stages')}</p>
			{/each}
		</section>

		<section class="card card-body card-hl gap-3">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Coffee size={16} class="shrink-0" aria-hidden="true" /> {t('tn.setup.breaks')}</h2>
				{#if canOperate}<button class="btn btn-outline btn-sm gap-1" type="button" onclick={() => openRule(null)}><Plus size={14} /> {t('tn.setup.add_rule')}</button>{/if}
			</div>
			<p class="text-xs text-muted">{t('tn.setup.breaks_help')}</p>
			{#each setup.break_rules as r, i (r.id)}
				<div class="flex flex-wrap items-center gap-3 rounded-lg border border-line p-3">
					<div class="min-w-0 flex-1">
						<span class="text-sm text-ink">{r.summary}</span>
						<p class="font-mono text-xs text-muted">{fieldName_(r.field_id)}</p>
					</div>
					{#if canOperate}
						<span class="flex items-center gap-0.5">
							<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === 0 || run.busy} aria-label={t('tn.setup.rule_up')} title={t('tn.setup.rule_up')} onclick={() => moveRule(i, -1)}><ArrowUp size={14} /></button>
							<button class="btn btn-ghost btn-xs btn-square" type="button" disabled={i === setup.break_rules.length - 1 || run.busy} aria-label={t('tn.setup.rule_down')} title={t('tn.setup.rule_down')} onclick={() => moveRule(i, 1)}><ArrowDown size={14} /></button>
						</span>
						<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.setup.edit_rule')} onclick={() => openRule(r)}><Pencil size={13} /></button>
						<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.setup.delete_rule')} onclick={() => { delRule = r; run.clear(); delRuleOpen = true; }}><Trash2 size={13} /></button>
					{/if}
				</div>
			{:else}
				<p class="text-sm text-muted">{t('tn.setup.no_rules')}</p>
			{/each}
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
		<Field label={t('tn.setup.sequence')} type="number" min="1" bind:value={sf.sequence} error={run.fields.sequence} help={t('tn.setup.sequence_help')} />
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
<Toast message={run.notice} onhide={() => (run.notice = '')} />
