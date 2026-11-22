<script lang="ts">
	import { goto } from '$app/navigation';
	import { ArrowLeft, Check, Flag, Goal, Play, RotateCcw, ListOrdered, Pencil, Repeat, Shield, Timer, Trash2, Zap } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import LiveBadge from '#lib/components/tournaments/LiveBadge.svelte';
	import NoticeAlert from '#lib/components/NoticeAlert.svelte';
	import SearchSelect from '#lib/components/SearchSelect.svelte';
	import TeamLogo from '#lib/components/tournaments/TeamLogo.svelte';
	import { statusPill } from '#lib/components/tournaments/headerBadge.svelte';
	import { api } from '#lib/management-api.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { fmtDateTime, numOrNull, Runner, TBASE, type MatchEvent, type MatchEventType, type RosterPerson } from '#lib/tournaments.svelte.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);
	const canOperate = $derived(tn.can_operate);
	const game = $derived(data.game);
	const m = $derived(game?.match ?? null);
	const run = new Runner();

	const TYPES: { type: MatchEventType; icon: typeof Goal }[] = [
		{ type: 'goal', icon: Goal },
		{ type: 'penalty', icon: Shield },
		{ type: 'goalie_change', icon: Repeat },
		{ type: 'timeout', icon: Timer },
		{ type: 'other', icon: Zap }
	];
	type Form = { id: string; type: MatchEventType; side: 'home' | 'away'; minute: string; second: string; player_id: string; player: string; assist1: string; assist2: string; code: string; length: string; goalie_in: string; goalie_out: string; value: string; note: string };
	const blank = (keep?: Pick<Form, 'type' | 'side'>): Form => ({ id: '', type: keep?.type ?? 'goal', side: keep?.side ?? 'home', minute: '', second: '', player_id: '', player: '', assist1: '', assist2: '', code: '', length: '', goalie_in: '', goalie_out: '', value: '', note: '' });
	let f = $state<Form>(blank());

	const roster = $derived<RosterPerson[]>(game?.rosters[f.side] ?? []);
	const hasRoster = $derived(roster.length > 0);
	const teamName = (side: 'home' | 'away' | null) => (side === 'home' ? m?.home_display : side === 'away' ? m?.away_display : '') ?? '';
	/** Searchable lists: players by number and name, the penalty codes by name and code. */
	const items = (list: RosterPerson[]) => list.map((p) => ({ id: p.id, label: who(p), sub: p.position ?? '' }));
	/** A goalie change picks goalies; a team without any goalie marked lists everyone so the entry is never blocked. */
	const goalies = $derived(roster.some((p) => p.is_goalie) ? roster.filter((p) => p.is_goalie) : roster);
	const codeItems = $derived((game?.penalty_codes ?? []).map((c) => ({ id: c.code, label: `${c.name} · ${c.code}` })));
	const who = (p: RosterPerson) => `${p.number !== null ? `#${p.number} ` : ''}${p.name}`;
	const clockText = (e: { minute: number | null; second: number | null }) => (e.minute === null ? '' : `${e.minute}:${String(e.second ?? 0).padStart(2, '0')}`);
	const typeLabel = (type: string) => t(`tn.scores.ev_${type}`);

	/** Changing the team empties the player picks: they belong to the other roster. */
	function setSide(side: 'home' | 'away') {
		if (f.side === side) return;
		f = { ...f, side, player_id: '', assist1: '', assist2: '', goalie_in: '', goalie_out: '' };
	}
	function setType(type: MatchEventType) {
		f = { ...blank({ type, side: f.side }), id: f.id, minute: f.minute, second: f.second };
	}
	function edit(e: MatchEvent) {
		f = {
			id: e.id,
			type: e.type,
			side: e.side ?? 'home',
			minute: e.minute === null ? '' : String(e.minute),
			second: e.second === null ? '' : String(e.second),
			player_id: e.player_id ?? '',
			player: e.player_id ? '' : e.player,
			assist1: e.assists[0]?.id ?? '',
			assist2: e.assists[1]?.id ?? '',
			code: e.penalty_code,
			length: e.penalty_length,
			goalie_in: e.goalie_in?.id ?? '',
			goalie_out: e.goalie_out?.id ?? '',
			value: e.value === null || game?.ice_hockey ? '' : String(e.value),
			note: e.note
		};
		window.scrollTo({ top: 0, behavior: 'smooth' });
	}
	/** Cancel leaves the entry being edited, or the screen when nothing is being edited. */
	const cancel = () => (f.id ? (f = blank({ type: f.type, side: f.side })) : goto(`${TBASE}/${tn.id}/scores`));

	function body() {
		const common = { type: f.type, side: f.side, minute: numOrNull(f.minute), second: f.second === '' && f.minute !== '' ? 0 : numOrNull(f.second), note: f.note.trim() };
		if (f.type === 'goal') return { ...common, player_id: f.player_id || null, player: hasRoster ? '' : f.player.trim(), ...(game?.ice_hockey ? { assist_ids: [f.assist1, f.assist2].filter(Boolean) } : {}) };
		if (f.type === 'penalty') {
			return {
				...common,
				player_id: f.player_id || null,
				player: hasRoster ? '' : f.player.trim(),
				...(game?.ice_hockey ? { penalty_code: f.code, penalty_length: f.length } : { value: numOrNull(f.value), penalty_code: f.code.trim() })
			};
		}
		if (f.type === 'goalie_change') return { ...common, goalie_in_id: f.goalie_in || null, goalie_out_id: f.goalie_out || null };
		if (f.type === 'timeout') return common;
		return { ...common, player_id: f.player_id || null, player: hasRoster ? '' : f.player.trim() };
	}
	async function save(e: SubmitEvent) {
		e.preventDefault();
		run.clear();
		const keep = { type: f.type, side: f.side };
		const call = () => (f.id ? api('PATCH', `/tournaments/matches/events/${f.id}`, { body: body() }) : api('POST', `/tournaments/matches/${data.matchId}/events`, { body: body() }));
		const ok = await run.run(async () => {
			const res = await call();
			if (!res.ok && res.code === 'match_complete') res.message = t('tn.scores.match_complete');
			return res;
		}, undefined, { notice: t('tn.game.saved') });
		if (ok) f = blank(keep);
	}
	async function remove(x: MatchEvent) {
		await run.run(async () => {
			const res = await api('DELETE', `/tournaments/matches/events/${x.id}`);
			if (!res.ok && res.code === 'match_complete') res.message = t('tn.scores.match_complete');
			return res;
		}, undefined, { notice: t('tn.game.deleted') });
		if (f.id === x.id) f = blank({ type: f.type, side: f.side });
	}

	// --- complete the game: the final score is checked and confirmed in a dialog, then the game is closed
	const complete = $derived(m?.status === 'complete');
	const live = $derived(m?.status === 'in_progress');
	// A game that has not started shows a big Start button (and, in ice hockey, the goalies in goal) instead of the entry form.
	// svelte-ignore state_referenced_locally
	let sg = $state({ home: data.game?.match.home_goalie_id ?? '', away: data.game?.match.away_goalie_id ?? '' });
	const goalieItems = (side: 'home' | 'away') => {
		const list = game?.rosters[side] ?? [];
		return items(list.some((p) => p.is_goalie) ? list.filter((p) => p.is_goalie) : list);
	};
	const startable = $derived(!!m && !!m.home_team && !!m.away_team);
	const start = () =>
		run.run(
			async () => {
				const res = await api('POST', `/tournaments/matches/${data.matchId}/start`, { body: { home_goalie_id: sg.home || null, away_goalie_id: sg.away || null } });
				if (!res.ok && res.code === 'teams_unknown') res.message = t('tn.game.teams_unknown');
				return res;
			},
			undefined,
			{ notice: t('tn.game.started') }
		);
	/** The goalie in goal for a side: the one of the last goalie change, else the one the game started with. */
	function currentGoalie(side: 'home' | 'away'): string {
		const last = [...(game?.events ?? [])].reverse().find((e) => e.type === 'goalie_change' && e.side === side && e.goalie_in);
		return last?.goalie_in?.id ?? (side === 'home' ? m?.home_goalie_id : m?.away_goalie_id) ?? '';
	}
	$effect(() => {
		if (f.type === 'goalie_change' && !f.id && !f.goalie_out) f.goalie_out = currentGoalie(f.side);
	});
	let doneOpen = $state(false);
	let rf = $state({ home: '', away: '', ot: false, ph: '', pa: '' });
	function openComplete() {
		rf = { home: String(m?.score_home ?? 0), away: String(m?.score_away ?? 0), ot: m?.went_to_overtime ?? false, ph: '', pa: '' };
		run.clear();
		doneOpen = true;
	}
	// A game in several periods: closing a period only saves that period; the last one completes the whole game.
	const periods = $derived(game?.legs ?? []);
	const isTie = $derived(periods.length > 1);
	const periodNo = $derived(isTie ? (periods.find((p) => p.id === m?.id)?.leg ?? 1) : 1);
	const nextPeriod = $derived(isTie ? (periods.find((p) => p.leg === periodNo + 1) ?? null) : null);
	const otherPeriods = $derived(periods.filter((p) => p.id !== m?.id));
	/** The score of the whole game as typed in the dialog: the other periods as saved plus this one. */
	const gameHome = $derived(otherPeriods.reduce((n, p) => n + (p.score_home ?? 0), 0) + Number(rf.home || 0));
	const gameAway = $derived(otherPeriods.reduce((n, p) => n + (p.score_away ?? 0), 0) + Number(rf.away || 0));
	const level = $derived(rf.home !== '' && rf.away !== '' && !nextPeriod && gameHome === gameAway);
	async function confirmComplete(e: SubmitEvent) {
		e.preventDefault();
		const body = { score_home: Number(rf.home), score_away: Number(rf.away), went_to_overtime: nextPeriod ? false : rf.ot, penalty_home: level ? numOrNull(rf.ph) : null, penalty_away: level ? numOrNull(rf.pa) : null };
		const nextId = nextPeriod?.id;
		const goalies = { home_goalie_id: currentGoalie('home') || null, away_goalie_id: currentGoalie('away') || null };
		if (await run.post(`/tournaments/matches/${data.matchId}/result`, body, undefined, { notice: nextId ? undefined : t('tn.game.completed_done'), reload: !nextId })) {
			doneOpen = false;
			if (nextId) {
				await api('POST', `/tournaments/matches/${nextId}/start`, { body: goalies }); // the next period starts by itself, with the goalies that were in goal
				await goto(`${TBASE}/${tn.id}/matches/${nextId}`);
			} else {
				await goto(`${TBASE}/${tn.id}/scores`);
			}
		}
	}

	const reopen = () =>
		run.run(
			async () => {
				const res = await api('POST', `/tournaments/matches/${data.matchId}/reopen`, { body: {} });
				if (!res.ok && res.code === 'has_dependents_played') res.message = t('tn.game.reopen_blocked');
				return res;
			},
			undefined,
			{ notice: t('tn.game.reopened') }
		);

	/** What an event says, in one line. */
	function summary(e: MatchEvent): string {
		const person = e.player_number !== null && e.player_id ? `#${e.player_number} ${e.player}` : e.player;
		if (e.type === 'goal') {
			const assists = e.assists.map(who).join(', ');
			return [person || t('tn.game.unknown_player'), assists ? `(${t('tn.game.assists_short')}: ${assists})` : ''].filter(Boolean).join(' ');
		}
		if (e.type === 'penalty') {
			const what = [e.penalty_code, e.penalty_name && e.penalty_name !== e.penalty_code ? e.penalty_name : '', e.penalty_length ? `${e.penalty_length} min` : e.value !== null ? `${e.value} min` : ''].filter(Boolean).join(' · ');
			return `${what} — ${person || t('tn.game.team_penalty')}`;
		}
		if (e.type === 'goalie_change') return `${e.goalie_out ? `${t('tn.game.out')} ${who(e.goalie_out)}` : ''}${e.goalie_out && e.goalie_in ? ' → ' : ''}${e.goalie_in ? `${t('tn.game.in')} ${who(e.goalie_in)}` : ''}`;
		return [person, e.note].filter(Boolean).join(' — ');
	}

	usePageHeader(() => ({ badge: statusPill, title: tn.name, context: t('tn.game.title') }));
</script>

<svelte:head><title>{pageTitle(t('tn.game.title'))}</title></svelte:head>

<div class="grid min-w-0 gap-4">
	<a class="link inline-flex w-fit items-center gap-1 text-sm" href="{TBASE}/{tn.id}/scores"><ArrowLeft size={14} /> {t('tn.game.back')}</a>
	<NoticeAlert message={run.notice} onhide={() => (run.notice = '')} />
	{#if data.error || !game || !m}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		<section class="card card-body card-hl relative gap-2">
			<p class="text-xs text-muted">{[m.round_label || t('tn.standings.round', { n: m.round_number }), m.scheduled_start ? fmtDateTime(m.scheduled_start) : '', m.field?.name, m.leg ? t('tn.leg', { n: m.leg }) : ''].filter(Boolean).join(' · ')}</p>
			{#if live}<div class="absolute top-4 right-4"><LiveBadge /></div>{/if}
			<div class="grid grid-cols-[1fr_auto_1fr] items-center gap-4">
				<div class="flex items-center justify-end gap-2 text-right font-display text-lg font-extrabold text-ink">
					{m.home_display}
					{#if m.home_team}<TeamLogo name={m.home_team.name} logoUrl={m.home_team.logo_url} color={m.home_team.primary_color} size="size-9" />{/if}
				</div>
				<div class="rounded-lg bg-ink px-4 py-1.5 font-mono text-2xl font-bold text-white">{isTie ? periods.reduce((n, p) => n + (p.score_home ?? 0), 0) : (m.score_home ?? '–')} : {isTie ? periods.reduce((n, p) => n + (p.score_away ?? 0), 0) : (m.score_away ?? '–')}</div>
				<div class="flex items-center gap-2 font-display text-lg font-extrabold text-ink">
					{#if m.away_team}<TeamLogo name={m.away_team.name} logoUrl={m.away_team.logo_url} color={m.away_team.primary_color} size="size-9" />{/if}
					{m.away_display}
				</div>
			</div>
			{#if game.legs.length > 1}
				<!-- one game in several periods: each period has its own timeline; the switch moves between them, the total is the whole game -->
				<div class="flex flex-wrap items-center justify-center gap-2" role="group" aria-label={t('tn.scores.legs')}>
					{#each game.legs as pl (pl.id)}
						<a href="{TBASE}/{tn.id}/matches/{pl.id}" aria-current={pl.id === m.id ? 'page' : undefined} class="rounded-lg border-2 px-4 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {pl.id === m.id ? 'border-club bg-club/10 text-ink' : 'border-line bg-white text-muted hover:text-ink'}">
							{t('tn.leg', { n: pl.leg })}
							<span class="ml-1 font-mono font-normal normal-case">{pl.score_home ?? '–'}:{pl.score_away ?? '–'}</span>
							{#if pl.status === 'in_progress'}<span class="ml-1 inline-block size-2 animate-pulse rounded-full bg-danger align-middle"></span>{/if}
						</a>
					{/each}
				</div>
			{/if}
			<p class="text-center text-xs text-muted">{t('tn.game.help')}</p>
			{#if complete}
				<div class="mx-auto flex flex-wrap items-center justify-center gap-3 rounded-lg bg-ok-bg px-4 py-2 text-sm font-semibold text-ok-text" role="status">
					<span class="flex items-center gap-2"><Check size={16} aria-hidden="true" /> {t('tn.game.is_complete')}</span>
					{#if canOperate}<button class="btn btn-outline btn-sm gap-1 bg-white" type="button" disabled={run.busy} onclick={reopen}><RotateCcw size={14} aria-hidden="true" /> {t('tn.game.reopen')}</button>{/if}
				</div>
			{:else if canOperate && live}
				<div class="flex justify-center"><button class="btn btn-primary gap-2" type="button" onclick={openComplete}><Flag size={16} aria-hidden="true" /> {nextPeriod ? t('tn.game.close_period', { n: periodNo }) : t('tn.game.complete')}</button></div>
			{/if}
		</section>

		<div class="grid items-start gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
			{#if canOperate && !complete && !live}
				<section class="card card-body items-center gap-5 py-10 text-center">
					<span class="flex size-16 items-center justify-center rounded-full bg-club/10 text-club"><Play size={30} aria-hidden="true" /></span>
					<div class="grid gap-1">
						<h2 class="font-display text-xl font-extrabold tracking-[.06em] text-ink uppercase">{t('tn.game.not_started')}</h2>
						<p class="text-sm text-muted">{t('tn.game.not_started_help')}</p>
					</div>
					{#if game.ice_hockey}
						<div class="grid w-full max-w-md gap-3 text-left">
							<p class="text-center text-xs font-semibold tracking-[.08em] text-muted uppercase">{t('tn.game.goalies_in_goal')}</p>
							<div class="form-control"><span class="label-text mb-1 text-sm font-medium">{m.home_display}</span><SearchSelect id="sg-home" items={goalieItems('home')} bind:value={sg.home} placeholder={t('tn.game.pick_goalie')} /></div>
							<div class="form-control"><span class="label-text mb-1 text-sm font-medium">{m.away_display}</span><SearchSelect id="sg-away" items={goalieItems('away')} bind:value={sg.away} placeholder={t('tn.game.pick_goalie')} /></div>
						</div>
					{/if}
					{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
					<button class="btn btn-primary btn-lg gap-2 px-10" type="button" disabled={run.busy || !startable} onclick={start}><Play size={20} aria-hidden="true" /> {t('tn.game.start')}</button>
					{#if !startable}<p class="text-xs text-muted">{t('tn.game.teams_unknown')}</p>{/if}
				</section>
			{:else if canOperate && live}
				<form class="card card-body gap-4" onsubmit={save}>
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListOrdered size={16} class="shrink-0" aria-hidden="true" /> {f.id ? t('tn.scores.ev_edit') : t('tn.scores.ev_add')}</h2>
					{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}

					<div class="flex flex-wrap gap-1" role="group" aria-label={t('tn.scores.ev_type')}>
						{#each TYPES as ty (ty.type)}
							{@const Icon = ty.icon}
							<button type="button" aria-pressed={f.type === ty.type} onclick={() => setType(ty.type)} class="flex items-center gap-1.5 rounded-md border px-3 py-2 font-display text-xs font-bold tracking-[.06em] uppercase {f.type === ty.type ? 'border-ink bg-ink text-white' : 'border-line bg-white text-muted hover:text-ink'}">
								<Icon size={14} aria-hidden="true" /> {typeLabel(ty.type)}
							</button>
						{/each}
					</div>

					<div class="grid grid-cols-2 gap-2" role="group" aria-label={t('tn.scores.ev_side')}>
						{#each ['home', 'away'] as const as side (side)}
							<button type="button" aria-pressed={f.side === side} onclick={() => setSide(side)} class="truncate rounded-lg border-2 px-3 py-2.5 text-sm font-semibold {f.side === side ? 'border-club bg-club/10 text-ink' : 'border-line bg-white text-muted hover:text-ink'}">{teamName(side)}</button>
						{/each}
					</div>

					<fieldset class="grid gap-1">
						<legend class="label-text mb-1 text-sm font-medium">{tn.clock_direction === 'down' ? t('tn.game.time_left') : t('tn.game.time_played')}</legend>
						<div class="flex items-center gap-2">
							<input class="input input-bordered w-24 text-center font-mono" type="number" min="0" max="999" inputmode="numeric" placeholder="mm" aria-label={t('tn.game.minutes')} bind:value={f.minute} />
							<span class="font-mono text-lg font-bold text-muted">:</span>
							<input class="input input-bordered w-24 text-center font-mono" type="number" min="0" max="59" inputmode="numeric" placeholder="ss" aria-label={t('tn.game.seconds')} bind:value={f.second} />
							<span class="text-xs text-muted">{tn.clock_direction === 'down' ? t('tn.game.time_help_down') : t('tn.game.time_help')}</span>
						</div>
						{#if run.fields.minute || run.fields.second}<span class="text-xs text-error">{run.fields.minute ?? run.fields.second}</span>{/if}
					</fieldset>

					{#if f.type === 'goal' || f.type === 'penalty' || f.type === 'other'}
						<div class="form-control">
							<label class="label-text mb-1 text-sm font-medium" for="g-player">{f.type === 'goal' ? t('tn.game.scorer') : t('tn.scores.ev_player')}</label>
							{#if hasRoster}
								<SearchSelect id="g-player" items={items(roster)} bind:value={f.player_id} placeholder={f.type === 'penalty' ? t('tn.game.team_penalty') : t('tn.game.search_player')} />
							{:else}
								<input id="g-player" class="input input-bordered w-full" bind:value={f.player} placeholder={t('tn.game.player_free')} />
								<span class="mt-1 text-xs text-muted">{t('tn.game.no_roster')}</span>
							{/if}
							{#if run.fields.player_id}<span class="text-xs text-error">{run.fields.player_id}</span>{/if}
						</div>
					{/if}

					{#if f.type === 'goal' && game.ice_hockey}
						<div class="grid gap-3 sm:grid-cols-2">
							{#each [['assist1', 'tn.game.assist_1'], ['assist2', 'tn.game.assist_2']] as [key, label] (key)}
								<div class="form-control">
									<span class="label-text mb-1 text-sm font-medium">{t(label)}</span>
									<SearchSelect id="g-{key}" items={items(roster.filter((p) => p.id !== f.player_id && p.id !== (key === 'assist1' ? f.assist2 : f.assist1)))} bind:value={f[key as 'assist1' | 'assist2']} placeholder={t('tn.game.search_player')} />
								</div>
							{/each}
						</div>
						{#if run.fields.assist_ids}<span class="text-xs text-error">{run.fields.assist_ids}</span>{/if}
					{/if}

					{#if f.type === 'penalty'}
						{#if game.ice_hockey}
							<div class="form-control">
								<label class="label-text mb-1 text-sm font-medium" for="g-code">{t('tn.game.penalty_code')}</label>
								<SearchSelect id="g-code" items={codeItems} bind:value={f.code} placeholder={t('tn.game.pick_code')} />
								{#if run.fields.penalty_code}<span class="text-xs text-error">{run.fields.penalty_code}</span>{/if}
							</div>
							<fieldset class="grid gap-1">
								<legend class="label-text mb-1 text-sm font-medium">{t('tn.game.duration')}</legend>
								<div class="flex flex-wrap gap-1">
									{#each game.penalty_lengths as len (len)}
										<button type="button" aria-pressed={f.length === len} onclick={() => (f.length = len)} class="min-w-14 rounded-md border px-3 py-2 font-mono text-sm font-bold {f.length === len ? 'border-ink bg-ink text-white' : 'border-line bg-white text-muted hover:text-ink'}">{len}</button>
									{/each}
								</div>
								{#if run.fields.penalty_length}<span class="text-xs text-error">{run.fields.penalty_length}</span>{/if}
							</fieldset>
						{:else}
							<div class="grid gap-3 sm:grid-cols-2">
								<div class="form-control"><label class="label-text mb-1 text-sm font-medium" for="g-pcode">{t('tn.game.penalty_code')}</label><input id="g-pcode" class="input input-bordered w-full" bind:value={f.code} /></div>
								<div class="form-control"><label class="label-text mb-1 text-sm font-medium" for="g-pmin">{t('tn.scores.ev_value_penalty')}</label><input id="g-pmin" class="input input-bordered w-full" type="number" min="0" bind:value={f.value} /></div>
							</div>
						{/if}
					{/if}

					{#if f.type === 'goalie_change'}
						<div class="grid gap-3 sm:grid-cols-2">
							<div class="form-control">
								<label class="label-text mb-1 text-sm font-medium" for="g-out">{t('tn.game.goalie_out')}</label>
								<SearchSelect id="g-out" items={items(goalies.filter((p) => p.id !== f.goalie_in))} bind:value={f.goalie_out} placeholder={t('tn.game.search_player')} />
							</div>
							<div class="form-control">
								<label class="label-text mb-1 text-sm font-medium" for="g-in">{t('tn.game.goalie_in')}</label>
								<SearchSelect id="g-in" items={items(goalies.filter((p) => p.id !== f.goalie_out))} bind:value={f.goalie_in} placeholder={t('tn.game.search_player')} />
							</div>
						</div>
						{#if !hasRoster}<p class="text-xs text-muted">{t('tn.game.no_roster')}</p>{/if}
						{#if run.fields.goalie_in_id}<span class="text-xs text-error">{run.fields.goalie_in_id}</span>{/if}
					{/if}

					{#if f.type === 'other' || f.type === 'penalty' || f.type === 'goal'}
						<div class="form-control">
							<label class="label-text mb-1 text-sm font-medium" for="g-note">{t('tn.scores.ev_note')}</label>
							<input id="g-note" class="input input-bordered w-full" maxlength="255" bind:value={f.note} />
						</div>
					{/if}

					<div class="flex justify-end gap-2">
						<button class="btn btn-outline" type="button" onclick={cancel}>{t('common.cancel')}</button>
						<button class="btn btn-primary gap-2" type="submit" disabled={run.busy || (f.type === 'penalty' && !!game.ice_hockey && (!f.code || !f.length))}><Check size={16} aria-hidden="true" /> {t('tn.scores.ev_save')}</button>
					</div>
				</form>
			{/if}

			<section class="card card-body gap-3 {canOperate && !complete ? '' : 'lg:col-span-2'}">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListOrdered size={16} class="shrink-0" aria-hidden="true" /> {t('tn.game.timeline')}</h2>
				{#if game.events.length}
					<div class="overflow-x-auto rounded-lg border border-line">
						<table class="table table-sm w-full">
							<thead>
								<tr><th class="w-16">{t('tn.game.time')}</th><th>{t('tn.scores.ev_side')}</th><th>{t('tn.scores.ev_type')}</th><th>{t('tn.game.details')}</th>{#if canOperate && !complete}<th class="w-24"></th>{/if}</tr>
							</thead>
							<tbody>
								{#each game.events as e (e.id)}
									<tr class="hover:bg-paper {f.id === e.id ? 'bg-row-sel' : ''}">
										<td class="font-mono text-xs font-semibold whitespace-nowrap">{clockText(e) || '—'}</td>
										<td class="text-sm whitespace-nowrap">{teamName(e.side) || '—'}</td>
										<td class="text-sm font-semibold whitespace-nowrap">{typeLabel(e.type)}</td>
										<td class="text-sm">{summary(e)}</td>
										{#if canOperate && !complete}
											<td>
												<div class="flex items-center justify-end gap-1">
													<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('tn.scores.ev_edit')} title={t('tn.scores.ev_edit')} onclick={() => edit(e)}><Pencil size={13} /></button>
													<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('tn.scores.ev_delete')} title={t('tn.scores.ev_delete')} disabled={run.busy} onclick={() => remove(e)}><Trash2 size={13} /></button>
												</div>
											</td>
										{/if}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{:else}
					<p class="text-sm text-muted">{t('tn.scores.ev_empty')}</p>
				{/if}
			</section>
		</div>
	{/if}
</div>

<Modal bind:open={doneOpen} title={nextPeriod ? t('tn.game.close_period', { n: periodNo }) : t('tn.game.complete_title')}>
	<form class="grid gap-4" onsubmit={confirmComplete}>
		{#if run.error}<Alert kind="error">{run.error}</Alert>{/if}
		<p class="text-sm text-muted">{nextPeriod ? t('tn.game.close_period_help', { n: periodNo, next: periodNo + 1 }) : isTie ? t('tn.game.complete_game_help') : t('tn.game.complete_help')}</p>
		{#if isTie && !nextPeriod}
			<div class="grid gap-1 rounded-lg border border-line bg-paper p-3 text-sm">
				{#each otherPeriods as p (p.id)}<div class="flex justify-between"><span class="text-muted">{t('tn.leg', { n: p.leg })}</span><span class="font-mono font-semibold">{p.score_home ?? 0}:{p.score_away ?? 0}</span></div>{/each}
				<div class="flex justify-between"><span class="text-muted">{t('tn.leg', { n: periodNo })}</span><span class="font-mono font-semibold">{rf.home || 0}:{rf.away || 0}</span></div>
				<div class="flex justify-between border-t border-line pt-1"><span class="font-semibold text-ink">{t('tn.game.whole_game')}</span><span class="font-mono text-base font-extrabold text-ink">{gameHome}:{gameAway}</span></div>
			</div>
		{/if}
		<div class="grid grid-cols-[1fr_auto_1fr] items-end gap-3">
			<label class="form-control"><span class="label-text mb-1 truncate text-sm font-medium">{m?.home_display}</span><input class="input input-bordered w-full text-center font-mono text-2xl font-bold" type="number" min="0" required bind:value={rf.home} /></label>
			<span class="pb-2 font-mono text-2xl font-bold text-muted">:</span>
			<label class="form-control"><span class="label-text mb-1 truncate text-sm font-medium">{m?.away_display}</span><input class="input input-bordered w-full text-center font-mono text-2xl font-bold" type="number" min="0" required bind:value={rf.away} /></label>
		</div>
		{#if run.fields.score_away || run.fields.score_home}<span class="text-xs text-error">{run.fields.score_away ?? run.fields.score_home}</span>{/if}
		{#if !nextPeriod}<label class="flex cursor-pointer items-center gap-3 rounded-lg border border-line p-3"><input type="checkbox" class="checkbox" bind:checked={rf.ot} /><span class="grid"><span class="text-sm">{t('tn.scores.overtime')}</span><span class="text-xs text-muted">{t('tn.scores.overtime_help')}</span></span></label>{/if}
		{#if level}
			<div class="grid gap-2 rounded-lg border border-line p-3">
				<p class="text-sm font-semibold text-ink">{t('tn.scores.shots_title')}</p>
				<p class="text-xs text-muted">{t('tn.game.complete_shots_help')}</p>
				<div class="grid grid-cols-2 gap-3">
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.pen_home')}</span><input class="input input-bordered w-full" type="number" min="0" bind:value={rf.ph} /></label>
					<label class="form-control"><span class="label-text mb-1">{t('tn.scores.pen_away')}</span><input class="input input-bordered w-full" type="number" min="0" bind:value={rf.pa} /></label>
				</div>
				{#each ['penalty_home', 'penalty_away'] as k (k)}{#if run.fields[k]}<span class="text-xs text-error">{run.fields[k]}</span>{/if}{/each}
			</div>
		{/if}
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (doneOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={run.busy}><Check size={16} aria-hidden="true" /> {nextPeriod ? t('tn.game.close_period_confirm', { n: periodNo }) : t('tn.game.complete_confirm')}</button>
		</div>
	</form>
</Modal>
