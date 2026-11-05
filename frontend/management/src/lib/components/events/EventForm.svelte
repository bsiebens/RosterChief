<script lang="ts">
	import SearchSelect from '#lib/components/SearchSelect.svelte';
	import { tick, untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import {
		ArrowLeft,
		BusFront,
		CalendarDays,
		CircleCheck,
		Clock,
		Dumbbell,
		ExternalLink,
		FilePenLine,
		House,
		PartyPopper,
		Presentation,
		Repeat as RepeatIcon,
		Save,
		Shapes,
		Swords,
		Trash2,
		Trophy,
		Users
	} from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import EventFormAudience from '#lib/components/events/EventFormAudience.svelte';
	import EventFormRecurrence from '#lib/components/events/EventFormRecurrence.svelte';
	import EventFormScope from '#lib/components/events/EventFormScope.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { EditScope, EventOptions } from '#lib/events/types.ts';
	import {
		clubNow,
		createPayload,
		describeRecurrence,
		diffDays,
		diffMinutes,
		formatLocal,
		gameTitle as buildGameTitle,
		isAwayGame,
		isGame,
		normLocal,
		patchPayload,
		scopeBlocked,
		SHOWN_FIELDS,
		mondayBefore,
		shiftLocal,
		titleOptional,
		type FormState
	} from '#lib/events/form-types.ts';

	/**
	 * The add / edit event form (one component for both pages). Posts `payload` (JSON: the POST body, or for `edit` the
	 * PATCH diff against `initial`) and `scope` to the page's `?/save` action, which answers with a redirect or a
	 * `fail()` carrying the API's field errors (`fields`), `message` and `status`. For an occurrence of a series
	 * (`series`) the scope is asked in a dialog before saving.
	 *
	 * Layout: a full-width "Kind" card, then (wide screens) a main column (Details, When, Repeat) and a side column
	 * (Audience, and a sticky Save card with a summary of what will be saved).
	 */
	let {
		mode,
		options,
		initial,
		fields = {},
		message = '',
		status = 0,
		cancelHref,
		series = null,
		ondelete
	}: {
		mode: 'create' | 'edit';
		options: EventOptions;
		initial: FormState;
		fields?: Record<string, string>;
		message?: string;
		status?: number;
		cancelHref: string;
		/** Edit an occurrence of a series: the API's rule description and series first date - this occurrence's date (days). */
		series?: { description: string; shiftDays: number; detached: boolean } | null;
		ondelete?: () => void;
	} = $props();

	// svelte-ignore state_referenced_locally
	let v = $state<FormState>(structuredClone($state.snapshot(initial)));
	// The loaded values to diff against (edit).
	const base = untrack(() => structuredClone($state.snapshot(initial)) as FormState);
	let lastStart = untrack(() => v.start);
	let busy = $state(false);
	let tried = $state(false);
	let formEl: HTMLFormElement | undefined = $state();
	let scopeOpen = $state(false);
	let scope = $state<EditScope>('this');
	let scopeConfirmed = false;
	const uid = $props.id();

	const KIND_ICONS: Record<string, typeof Users> = {
		training: Dumbbell,
		game: Swords,
		tournament: Trophy,
		meeting: Presentation,
		social: PartyPopper,
		other: CalendarDays
	};

	const game = $derived(isGame(v.kind));
	// A new series cannot be "friendly"; an occurrence can (scope "this" only, see EventFormScope).
	const repeating = $derived(mode === 'create' && (v.repeat !== 'none' || !!v.rrule.trim()));
	const titleRequired = $derived(!game && (mode === 'edit' || !titleOptional(v.kind)));
	const recurrenceShown = $derived(mode === 'create' || !!series);
	const kindLabel = (value: string) => {
		const key = `eventform.kind.${value}`;
		const own = t(key);
		return own !== key ? own : (options.kinds.find((k) => k.value === value)?.label ?? value);
	};

	// Generated game title exactly as the API builds it (home side first; an away location puts the opponent first).
	const gameTitle = $derived(buildGameTitle(v, options, options.kinds.find((k) => k.value === 'game')?.label || kindLabel('game')));
	const away = $derived(isAwayGame(v, options));
	const opponent = $derived(options.opponents.find((o) => o.id === v.opponent_id) ?? null);
	const location = $derived(options.locations.find((l) => l.id === v.location_id) ?? null);
	const locationItems = $derived(options.locations.map((l) => ({ id: l.id, label: l.name, sub: [l.address, l.city].filter(Boolean).join(', '), badge: l.is_home ? t('eventform.location_home_tag') : undefined })));
	const opponentItems = $derived(options.opponents.map((o) => ({ id: o.id, label: o.name })));
	const isPast = $derived(!!v.start && normLocal(v.start) < clubNow());
	const wasPast = $derived(mode === 'edit' && !!base.start && normLocal(base.start) < clubNow());

	// -- client checks (the API checks the same and more) --
	const clientErrors = $derived.by(() => {
		const e: Record<string, string> = {};
		if (!v.start) e.start = t('eventform.err.start_required');
		if (v.start && v.end && (diffMinutes(v.start, v.end) ?? 1) <= 0) e.end = t('eventform.err.end_after_start');
		if (v.start && v.gathering && (diffMinutes(v.gathering, v.start) ?? 0) < 0) e.gathering = t('eventform.err.gathering_before_start');
		if (v.start && v.deadline && (diffMinutes(v.deadline, v.start) ?? 0) < 0) e.deadline = t('eventform.err.deadline_before_start');
		if (titleRequired && !v.title.trim()) e.title = t('eventform.err.title_required');
		if (game && !v.club_wide && !v.team_ids.length) e.team_ids = t('eventform.err.game_team');
		if (recurrenceShown && !v.rrule.trim() && v.repeat === 'weekly' && !v.weekdays.length) e.weekdays = t('eventform.err.weekdays');
		if (recurrenceShown && !v.rrule.trim() && v.repeat !== 'none' && v.ends === 'until' && !v.until) e.until = t('eventform.err.until_required');
		return e;
	});
	const shown = $derived<Record<string, string>>({ ...(tried ? clientErrors : {}), ...fields });
	const fe = (k: string) => shown[k];
	const otherErrors = $derived(Object.entries(fields).filter(([k]) => !SHOWN_FIELDS.includes(k)));
	const formError = $derived(status === 403 ? t('eventform.err.forbidden') : status === 0 && message === 'network' ? t('common.error.network') : message && !Object.keys(fields).length ? message : Object.keys(fields).length ? t('eventform.err.fix_fields') : '');

	// -- edit: what changed, and which scopes allow it --
	const patch = $derived(mode === 'edit' ? patchPayload(v, base, { seriesShiftDays: series?.shiftDays ?? 0, allowRecurrence: !!series }) : null);
	const scopeLimits = $derived.by(() => ({
		recurrence: !!patch?.recurrence,
		dateMoved: !!v.start && !!base.start && diffDays(base.start, v.start) !== 0,
		friendly: !!patch && 'is_friendly' in patch.body
	}));
	const unchanged = $derived(!!patch && !Object.keys(patch.body).length);

	// -- summary in the Save card --
	const summaryWhen = $derived.by(() => {
		if (!v.start) return t('eventform.sum.no_start');
		const start = formatLocal(normLocal(v.start));
		const end = normLocal(v.end);
		if (!end) return start;
		return `${start} – ${end.slice(0, 10) === normLocal(v.start).slice(0, 10) ? end.slice(11, 16) : formatLocal(end)}`;
	});
	const summaryWho = $derived.by(() => {
		const parts: string[] = [];
		if (v.club_wide) parts.push(t('eventform.audience.club_wide'));
		else {
			const teams = [...options.teams, ...(options.filter_teams ?? [])];
			const groups = [...options.groups, ...(options.filter_groups ?? [])];
			const names = [...v.team_ids.map((id) => teams.find((x) => x.id === id)?.name), ...v.group_ids.map((id) => groups.find((x) => x.id === id)?.name)].filter((x) => !!x);
			if (names.length) parts.push(names.join(', '));
		}
		if (v.invited.length) parts.push(t('eventform.sum.invited', { n: v.invited.length }));
		if (v.excluded.length) parts.push(t('eventform.sum.excluded', { n: v.excluded.length }));
		return parts.length ? parts.join(' · ') : t('eventform.sum.nobody');
	});
	const summaryRepeat = $derived(describeRecurrence(v) || t('eventform.sum.once'));

	function onStartChange() {
		// End, gathering and deadline move along with the start (as the API does when only the start changes).
		const delta = lastStart && v.start ? diffMinutes(lastStart, v.start) : null;
		if (delta) for (const k of ['end', 'gathering', 'deadline'] as const) if (v[k]) v[k] = shiftLocal(v[k], delta);
		lastStart = v.start;
	}
	const fromStart = (minutes: number) => (v.start ? shiftLocal(v.start, minutes) : '');
	const hm = (m: number) => (m % 60 ? `${Math.floor(m / 60)}h${m % 60}` : `${m / 60}h`);

	async function showFirstError() {
		await tick();
		formEl?.querySelector<HTMLElement>('[aria-invalid="true"], .input-error, .select-error, .text-error')?.scrollIntoView({ block: 'center', behavior: 'smooth' });
	}

	function confirmScope(chosen: EditScope) {
		scope = chosen;
		scopeConfirmed = true;
		scopeOpen = false;
		formEl?.requestSubmit();
	}
</script>

{#snippet head(id: string, title: string, desc: string, Icon: typeof Users)}
	<header class="mb-2 grid gap-0.5">
		<h2 {id} class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Icon size={16} aria-hidden="true" /> {title}</h2>
		<p class="text-xs text-muted">{desc}</p>
	</header>
{/snippet}

{#snippet shortcuts(label: string, items: { label: string; run: () => void }[])}
	<div class="flex flex-wrap items-center justify-end gap-1" role="group" aria-label={t('eventform.short.group', { field: label })}>
		{#each items as it (it.label)}
			<button type="button" class="btn btn-ghost btn-xs rounded-full border border-line" disabled={!v.start} onclick={it.run}>{it.label}</button>
		{/each}
	</div>
{/snippet}

{#snippet summaryRow(Icon: typeof Users, label: string, value: string)}
	<div class="grid grid-cols-[1rem_minmax(0,1fr)] gap-x-2.5">
		<Icon size={15} class="mt-0.5 text-dim" aria-hidden="true" />
		<div class="min-w-0">
			<dt class="font-mono text-[11px] tracking-wide text-muted uppercase">{label}</dt>
			<dd class="text-sm break-words text-ink">{value}</dd>
		</div>
	</div>
{/snippet}

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	bind:this={formEl}
	use:enhance={({ formData, cancel }) => {
		tried = true;
		if (Object.keys(clientErrors).length) {
			cancel();
			showFirstError();
			return;
		}
		if (mode === 'edit') {
			const changes = patch!.body;
			if (!Object.keys(changes).length) {
				cancel();
				goto(cancelHref);
				return;
			}
			if (series && !scopeConfirmed) {
				cancel();
				scope = scopeBlocked('this', scopeLimits) ? 'all' : 'this';
				scopeOpen = true;
				return;
			}
			formData.set('payload', JSON.stringify({ ...changes, notify: v.notify }));
			formData.set('scope', series ? scope : 'this');
		} else {
			formData.set('payload', JSON.stringify(createPayload(v)));
		}
		scopeConfirmed = false;
		busy = true;
		return async ({ update, result }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'failure') showFirstError();
		};
	}}>
	{#if formError || otherErrors.length}
		<div class="grid gap-3">
			{#if formError}<Alert kind="error" title={status === 403 ? t('eventform.err.forbidden_title') : undefined}>{formError}</Alert>{/if}
			{#if otherErrors.length}
				<Alert kind="error">
					<ul class="list-disc pl-4">{#each otherErrors as [k, msg] (k)}<li><span class="font-mono text-xs">{k}</span>: {msg}</li>{/each}</ul>
				</Alert>
			{/if}
		</div>
	{/if}

	<!-- Kind: full width, one row of big tiles (3 x 2 on narrow screens) -->
	<section class="card card-body" aria-labelledby="{uid}-h-kind">
		{@render head(`${uid}-h-kind`, t('eventform.card.kind'), t('eventform.card.kind_desc'), Shapes)}
		<fieldset>
			<legend class="sr-only">{t('eventform.kinds_label')}</legend>
			<div class="grid grid-cols-3 gap-2 lg:grid-cols-6">
				{#each options.kinds as k (k.value)}
					{@const on = v.kind === k.value}
					{@const Icon = KIND_ICONS[k.value] ?? CalendarDays}
					<label
						class="flex min-h-22 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 px-2 py-3 text-center transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-club has-[:focus-visible]:ring-offset-2 {on
							? 'border-club bg-club text-club-content'
							: 'border-line bg-white text-slate hover:border-stroke hover:bg-subhead'}">
						<input class="sr-only" type="radio" name="{uid}-kind" value={k.value} bind:group={v.kind} />
						<Icon size={26} strokeWidth={on ? 2.25 : 1.75} aria-hidden="true" />
						<span class="font-display text-sm leading-tight font-bold tracking-wide uppercase">{kindLabel(k.value)}</span>
					</label>
				{/each}
			</div>
			{#if fe('kind')}<span class="mt-1 block text-xs text-error">{fe('kind')}</span>{/if}
		</fieldset>
	</section>

	<div class="grid gap-5 xl:grid-cols-3">
		<!-- Main column -->
		<div class="grid content-start gap-5 xl:col-span-2">
			<!-- Details -->
			<section class="card card-body" aria-labelledby="{uid}-h-details">
				{@render head(`${uid}-h-details`, t('eventform.card.details'), game ? t('eventform.card.details_desc') : t('eventform.card.details_desc_plain'), FilePenLine)}
				<div class="grid gap-4">
					{#if game}
						<div class="rounded-lg border border-line bg-subhead px-3 py-2.5" aria-live="polite">
							<div class="font-mono text-[11px] tracking-wide text-muted uppercase">{t('eventform.title_generated')}</div>
							<div class="font-display text-xl font-bold break-words text-ink">{gameTitle}</div>
							<div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
								{#if !location}
									<span class="inline-flex items-center gap-1 text-muted"><House size={13} aria-hidden="true" /> {t('eventform.no_location_home')}</span>
								{:else if away}
									<span class="badge badge-warning gap-1"><BusFront size={12} aria-hidden="true" /> {t('eventform.away_game')}</span>
								{:else}
									<span class="badge badge-success gap-1"><House size={12} aria-hidden="true" /> {t('eventform.home_game')}</span>
								{/if}
							</div>
							<div class="mt-1 text-xs text-muted">{t('eventform.title_generated_help')}</div>
							{#if fe('title')}<span class="mt-1 block text-xs text-error">{fe('title')}</span>{/if}
						</div>
					{/if}

					<div class="grid gap-4 sm:grid-cols-2">
						{#if !game}
							<!-- Not a game: the title sits beside the location, like the opponent does for a game. -->
							<Field
								label={titleRequired ? t('eventform.title') : t('eventform.title_optional')}
								bind:value={v.title}
								error={fe('title')}
								maxlength={255}
								autocomplete="off"
								placeholder={titleRequired ? '' : kindLabel(v.kind)}
								help={titleRequired ? undefined : t('eventform.title_kind_help', { kind: kindLabel(v.kind) })} />
						{:else}
							<div class="form-control w-full min-w-0">
								<label class="label" for="{uid}-opp"><span class="label-text">{t('eventform.opponent')}</span></label>
								<div class="flex w-full items-center gap-2">
									<span class="grid size-9 shrink-0 place-items-center overflow-hidden rounded-lg border border-line bg-white" aria-hidden="true">
										{#if opponent?.logo_url}<img src={opponent.logo_url} alt="" class="size-full object-contain p-0.5" />{:else}<Swords size={16} class="text-dim" />{/if}
									</span>
									<div class="min-w-0 flex-1"><SearchSelect id="{uid}-opp" items={opponentItems} bind:value={v.opponent_id} placeholder={t('eventform.opponent_search')} noneLabel={t('eventform.opponent_none')} error={fe('opponent_id')} label={t('eventform.opponent')} /></div>
								</div>
								{#if fe('opponent_id')}<span class="mt-1 block text-xs text-error">{fe('opponent_id')}</span>{/if}
								<a class="link mt-1 inline-flex items-center gap-1 self-start text-xs" href="/calendar/opponents" target="_blank" rel="noopener">{t('eventform.manage_opponents')} <ExternalLink size={11} aria-hidden="true" /></a>
							</div>
						{/if}
						<div class="form-control w-full min-w-0">
							<label class="label" for="{uid}-loc"><span class="label-text">{t('eventform.location')}</span></label>
							<SearchSelect id="{uid}-loc" items={locationItems} bind:value={v.location_id} placeholder={t('eventform.location_search')} noneLabel={t('eventform.location_none')} error={fe('location_id')} label={t('eventform.location')} />
							{#if fe('location_id')}<span class="mt-1 block text-xs text-error">{fe('location_id')}</span>{/if}
							<a class="link mt-1 inline-flex items-center gap-1 self-start text-xs" href="/calendar/locations" target="_blank" rel="noopener">{t('eventform.manage_locations')} <ExternalLink size={11} aria-hidden="true" /></a>
						</div>
					</div>

					{#if game && !repeating}
						<div>
							<label class="flex cursor-pointer items-center gap-3">
								<input type="checkbox" class="checkbox" bind:checked={v.is_friendly} />
								<span class="label-text">{t('eventform.friendly')}</span>
							</label>
							{#if fe('is_friendly')}<span class="mt-1 block text-xs text-error">{fe('is_friendly')}</span>{/if}
						</div>
					{/if}
				</div>
			</section>

			<!-- When -->
			<section class="card card-body" aria-labelledby="{uid}-h-when">
				{@render head(`${uid}-h-when`, t('eventform.card.when'), t('eventform.card.when_desc'), Clock)}
				{#if wasPast}<div class="mb-2"><Alert kind="info">{t('eventform.past_hint')}</Alert></div>{:else if isPast}<div class="mb-2"><Alert kind="warning">{t('eventform.past_new')}</Alert></div>{/if}
				<div class="grid gap-4 sm:grid-cols-2">
					<Field label={t('eventform.start')} type="datetime-local" bind:value={v.start} onchange={onStartChange} error={fe('start')} required help={t('eventform.tz_help')} />
					<Field label={t('eventform.end')} type="datetime-local" bind:value={v.end} min={v.start || undefined} error={fe('end')} help={t('eventform.end_help')}>
						{#snippet aside()}
							{@render shortcuts(t('eventform.end'), [60, 90, 120].map((m) => ({ label: t('eventform.short.duration', { d: hm(m) }), run: () => (v.end = fromStart(m)) })))}
						{/snippet}
					</Field>
					<Field label={t('eventform.gathering')} type="datetime-local" bind:value={v.gathering} max={v.start || undefined} error={fe('gathering')} help={t('eventform.gathering_help')}>
						{#snippet aside()}
							{@render shortcuts(t('eventform.gathering'), [30, 45, 60, 75].map((m) => ({ label: t('eventform.short.min_before', { n: m }), run: () => (v.gathering = fromStart(-m)) })))}
						{/snippet}
					</Field>
					<Field label={t('eventform.deadline')} type="datetime-local" bind:value={v.deadline} max={v.start || undefined} error={fe('deadline')} help={t('eventform.deadline_help')}>
						{#snippet aside()}
							{@render shortcuts(t('eventform.deadline'), [
								{ label: t('eventform.short.days_before', { n: 2 }), run: () => (v.deadline = fromStart(-2880)) },
								{ label: t('eventform.short.week_before'), run: () => (v.deadline = fromStart(-10080)) },
								{ label: t('eventform.short.weeks_before', { n: 2 }), run: () => (v.deadline = fromStart(-20160)) },
								{ label: t('eventform.short.monday_before'), run: () => (v.deadline = mondayBefore(v.start)) }
							])}
						{/snippet}
					</Field>
				</div>
			</section>

			<!-- Repeat (new events, and edits of a series occurrence) -->
			{#if recurrenceShown}
				<section class="card card-body" aria-labelledby="{uid}-h-repeat">
					{@render head(`${uid}-h-repeat`, t('eventform.card.repeat'), mode === 'create' ? t('eventform.card.repeat_desc') : t('eventform.card.repeat_desc_edit'), RepeatIcon)}
					<EventFormRecurrence
						bind:s={v}
						fields={shown}
						allowNone={mode === 'create'}
						current={series?.description ?? ''}
						note={series ? (series.detached ? t('eventform.rec.edit_note_detached') : t('eventform.rec.edit_note')) : t('eventform.rec.create_note')} />
				</section>
			{/if}
		</div>

		<!-- Side column -->
		<div class="flex flex-col gap-5">
			<section class="card card-body" aria-labelledby="{uid}-h-audience">
				{@render head(`${uid}-h-audience`, t('eventform.card.audience'), t('eventform.card.audience_desc'), Users)}
				<EventFormAudience bind:s={v} {options} fields={shown} />
			</section>

			<section class="card card-body gap-4 xl:sticky xl:top-20" aria-labelledby="{uid}-h-save">
				{@render head(`${uid}-h-save`, t('eventform.card.save'), mode === 'create' ? t('eventform.card.save_desc_create') : t('eventform.card.save_desc_edit'), CircleCheck)}
				<dl class="grid gap-2.5 rounded-lg border border-line bg-subhead p-3">
					{@render summaryRow(game ? Swords : (KIND_ICONS[v.kind] ?? CalendarDays), kindLabel(v.kind), game ? gameTitle : v.title.trim() || (titleRequired ? '—' : kindLabel(v.kind)))}
					{@render summaryRow(Clock, t('eventform.sum.when'), summaryWhen)}
					{@render summaryRow(Users, t('eventform.sum.who'), summaryWho)}
					{#if recurrenceShown}{@render summaryRow(RepeatIcon, t('eventform.sum.repeats'), summaryRepeat)}{/if}
				</dl>
				{#if unchanged}<p class="text-xs text-muted">{t('eventform.sum.no_changes')}</p>{:else if series}<p class="text-xs text-muted">{t('eventform.sum.scope_note')}</p>{/if}

				<label class="flex cursor-pointer items-start gap-3">
					<input type="checkbox" class="checkbox mt-0.5" bind:checked={v.notify} />
					<span class="grid">
						<span class="label-text">{mode === 'create' ? t('eventform.notify_create') : t('eventform.notify_edit')}</span>
						<span class="text-xs text-muted">{mode === 'create' ? t('eventform.notify_create_help') : t('eventform.notify_edit_help')}</span>
					</span>
				</label>
				{#if fe('scope')}<span class="block text-xs text-error">{fe('scope')}</span>{/if}

				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {busy ? t('eventform.saving') : mode === 'create' ? t('eventform.create') : t('eventform.save')}</button>
					<div class="flex flex-wrap items-center gap-2">
						<a class="btn btn-outline flex-1 gap-2" href={cancelHref}><ArrowLeft size={16} /> {t('common.cancel')}</a>
						{#if ondelete}
							<button class="btn btn-outline btn-error flex-1 gap-2" type="button" onclick={ondelete}><Trash2 size={16} /> {t('common.delete')}</button>
						{/if}
					</div>
				</div>
			</section>
		</div>
	</div>

	{#if series}
		<Modal bind:open={scopeOpen} title={t('eventform.scope.title')}>
			<EventFormScope bind:scope purpose="edit" limits={scopeLimits} />
			<div class="modal-action mt-4 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (scopeOpen = false)}>{t('common.cancel')}</button>
				<button class="btn btn-primary gap-2" type="button" disabled={!!scopeBlocked(scope, scopeLimits)} onclick={() => confirmScope(scope)}><Save size={16} /> {t('eventform.save')}</button>
			</div>
		</Modal>
	{/if}
</form>
