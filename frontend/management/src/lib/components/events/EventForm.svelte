<script lang="ts">
	import { tick, untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { ArrowLeft, CalendarClock, ExternalLink, History, MapPin, Save, Swords, Trash2, Users } from '@lucide/svelte';
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
		diffDays,
		diffMinutes,
		isGame,
		normLocal,
		patchPayload,
		scopeBlocked,
		SHOWN_FIELDS,
		shiftLocal,
		titleOptional,
		type FormState
	} from '#lib/events/form-types.ts';

	/**
	 * The add / edit event form (one component for both pages). Posts `payload` (JSON: the POST body, or for `edit` the
	 * PATCH diff against `initial`) and `scope` to the page's `?/save` action, which answers with a redirect or a
	 * `fail()` carrying the API's field errors (`fields`), `message` and `status`. For an occurrence of a series
	 * (`series`) the scope is asked in a dialog before saving.
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

	const game = $derived(isGame(v.kind));
	// A new series cannot be "friendly"; an occurrence can (scope "this" only, see EventFormScope).
	const repeating = $derived(mode === 'create' && (v.repeat !== 'none' || !!v.rrule.trim()));
	const titleRequired = $derived(!game && (mode === 'edit' || !titleOptional(v.kind)));
	const kindLabel = (value: string) => {
		const key = `eventform.kind.${value}`;
		const own = t(key);
		return own !== key ? own : (options.kinds.find((k) => k.value === value)?.label ?? value);
	};

	// Generated game title, as the backend builds it (home side first; this preview cannot tell home from away).
	const gameTitle = $derived.by(() => {
		const us = options.teams.filter((x) => v.team_ids.includes(x.id)).map((x) => x.name).join(', ');
		const them = options.opponents.find((o) => o.id === v.opponent_id)?.name ?? '';
		if (us && them) return `${us} vs ${them}`;
		return us || them || kindLabel('game');
	});
	const opponent = $derived(options.opponents.find((o) => o.id === v.opponent_id) ?? null);
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
		const recurrenceShown = mode === 'create' || !!series;
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

	function onStartChange() {
		// End, gathering and deadline move along with the start (as the API does when only the start changes).
		const delta = lastStart && v.start ? diffMinutes(lastStart, v.start) : null;
		if (delta) for (const k of ['end', 'gathering', 'deadline'] as const) if (v[k]) v[k] = shiftLocal(v[k], delta);
		lastStart = v.start;
	}
	const fromStart = (minutes: number) => (v.start ? shiftLocal(v.start, minutes) : '');

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

{#snippet section(title: string, Icon: typeof Users)}
	<h2 class="mt-8 mb-4 flex items-center gap-2 border-b border-rule pb-1.5 font-display text-xs font-bold tracking-[.1em] text-muted uppercase first:mt-0"><Icon size={14} aria-hidden="true" /> {title}</h2>
{/snippet}

{#snippet shortcuts(label: string, items: { label: string; minutes: number }[], apply: (m: number) => void)}
	<div class="mt-1.5 flex flex-wrap items-center gap-1" role="group" aria-label={label}>
		{#each items as it (it.minutes)}
			<button type="button" class="btn btn-ghost btn-xs border border-line" disabled={!v.start} onclick={() => apply(it.minutes)}>{it.label}</button>
		{/each}
	</div>
{/snippet}

<form
	method="POST"
	action="?/save"
	class="card card-body"
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
	{#if formError}
		<div class="mb-4"><Alert kind="error" title={status === 403 ? t('eventform.err.forbidden_title') : undefined}>{formError}</Alert></div>
	{/if}
	{#if otherErrors.length}
		<div class="mb-4">
			<Alert kind="error">
				<ul class="list-disc pl-4">{#each otherErrors as [k, msg] (k)}<li><span class="font-mono text-xs">{k}</span>: {msg}</li>{/each}</ul>
			</Alert>
		</div>
	{/if}

	<!-- What -->
	{@render section(t('eventform.section.what'), CalendarClock)}
	<div class="grid gap-4">
		<fieldset>
			<legend class="label-text mb-2">{t('eventform.kind')}</legend>
			<div class="flex flex-wrap gap-1.5">
				{#each options.kinds as k (k.value)}
					{@const on = v.kind === k.value}
					<label class="cursor-pointer rounded-full border px-3.5 py-1.5 text-sm font-semibold transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-club {on ? 'border-primary bg-primary text-primary-content' : 'border-stroke bg-white text-slate hover:border-slate'}">
						<input class="sr-only" type="radio" name="{uid}-kind" value={k.value} bind:group={v.kind} />
						{kindLabel(k.value)}
					</label>
				{/each}
			</div>
			{#if fe('kind')}<span class="mt-1 block text-xs text-error">{fe('kind')}</span>{/if}
		</fieldset>

		{#if game}
			<div class="rounded-lg border border-line bg-subhead px-3 py-2">
				<div class="font-mono text-[11px] tracking-wide text-muted uppercase">{t('eventform.title_generated')}</div>
				<div class="font-display text-lg font-bold text-ink">{gameTitle}</div>
				<div class="text-xs text-muted">{t('eventform.title_generated_help')}</div>
				{#if fe('title')}<span class="mt-1 block text-xs text-error">{fe('title')}</span>{/if}
			</div>
			<div class="grid gap-4 md:grid-cols-2">
				<div class="form-control w-full">
					<label class="label" for="{uid}-opp"><span class="label-text">{t('eventform.opponent')}</span></label>
					<div class="flex items-center gap-2">
						<span class="grid size-10 shrink-0 place-items-center overflow-hidden rounded-lg border border-line bg-white" aria-hidden="true">
							{#if opponent?.logo_url}<img src={opponent.logo_url} alt="" class="size-full object-contain p-0.5" />{:else}<Swords size={16} class="text-dim" />{/if}
						</span>
						<select id="{uid}-opp" class="select select-bordered w-full {fe('opponent_id') ? 'select-error' : ''}" bind:value={v.opponent_id}>
							<option value="">{t('eventform.opponent_none')}</option>
							{#each options.opponents as o (o.id)}<option value={o.id}>{o.name}</option>{/each}
						</select>
					</div>
					{#if fe('opponent_id')}<span class="mt-1 block text-xs text-error">{fe('opponent_id')}</span>{/if}
					<a class="link mt-1 inline-flex items-center gap-1 text-xs" href="/calendar/opponents" target="_blank" rel="noopener">{t('eventform.manage_opponents')} <ExternalLink size={11} aria-hidden="true" /></a>
				</div>
				{#if !repeating}
					<div class="form-control justify-end">
						<label class="label cursor-pointer justify-start gap-3 md:mb-7">
							<input type="checkbox" class="checkbox" bind:checked={v.is_friendly} />
							<span class="label-text">{t('eventform.friendly')}</span>
						</label>
						{#if fe('is_friendly')}<span class="block text-xs text-error">{fe('is_friendly')}</span>{/if}
					</div>
				{/if}
			</div>
		{:else}
			<Field
				label={titleRequired ? t('eventform.title') : t('eventform.title_optional')}
				bind:value={v.title}
				error={fe('title')}
				maxlength={255}
				autocomplete="off"
				placeholder={titleRequired ? '' : kindLabel(v.kind)}
				help={titleRequired ? undefined : t('eventform.title_kind_help', { kind: kindLabel(v.kind) })} />
		{/if}
	</div>

	<!-- When -->
	{@render section(t('eventform.section.when'), History)}
	{#if wasPast}<div class="mb-4"><Alert kind="info">{t('eventform.past_hint')}</Alert></div>{:else if isPast}<div class="mb-4"><Alert kind="warning">{t('eventform.past_new')}</Alert></div>{/if}
	<div class="grid gap-4 md:grid-cols-2">
		<div>
			<Field label={t('eventform.start')} type="datetime-local" bind:value={v.start} onchange={onStartChange} error={fe('start')} required help={t('eventform.tz_help')} />
		</div>
		<div>
			<Field label={t('eventform.end')} type="datetime-local" bind:value={v.end} min={v.start || undefined} error={fe('end')} />
			{@render shortcuts(t('eventform.end'), [60, 90, 120].map((m) => ({ label: t('eventform.short.duration', { d: m % 60 ? `${Math.floor(m / 60)}h${m % 60}` : `${m / 60}h` }), minutes: m })), (m) => (v.end = fromStart(m)))}
		</div>
		<div>
			<Field label={t('eventform.gathering')} type="datetime-local" bind:value={v.gathering} max={v.start || undefined} error={fe('gathering')} />
			{@render shortcuts(t('eventform.gathering'), [15, 30, 45, 60].map((m) => ({ label: t('eventform.short.min_before', { n: m }), minutes: m })), (m) => (v.gathering = fromStart(-m)))}
		</div>
		<div>
			<Field label={t('eventform.deadline')} type="datetime-local" bind:value={v.deadline} max={v.start || undefined} error={fe('deadline')} help={t('eventform.deadline_help')} />
			{@render shortcuts(
				t('eventform.deadline'),
				[
					{ label: t('eventform.short.hours_before', { n: 2 }), minutes: 120 },
					{ label: t('eventform.short.day_before'), minutes: 1440 },
					{ label: t('eventform.short.days_before', { n: 2 }), minutes: 2880 },
					{ label: t('eventform.short.week_before'), minutes: 10080 }
				],
				(m) => (v.deadline = fromStart(-m))
			)}
		</div>
	</div>

	{#if mode === 'create' || series}
		{@render section(t('eventform.section.repeat'), History)}
		<EventFormRecurrence
			bind:s={v}
			fields={shown}
			allowNone={mode === 'create'}
			current={series?.description ?? ''}
			note={series ? (series.detached ? t('eventform.rec.edit_note_detached') : t('eventform.rec.edit_note')) : t('eventform.rec.create_note')} />
	{/if}

	<!-- Where -->
	{@render section(t('eventform.section.where'), MapPin)}
	<div class="form-control w-full md:max-w-xl">
		<label class="label" for="{uid}-loc"><span class="label-text">{t('eventform.location')}</span></label>
		<select id="{uid}-loc" class="select select-bordered w-full {fe('location_id') ? 'select-error' : ''}" bind:value={v.location_id}>
			<option value="">{t('eventform.location_none')}</option>
			{#each options.locations as l (l.id)}<option value={l.id}>{l.name}{l.city ? ` · ${l.city}` : ''}</option>{/each}
		</select>
		{#if fe('location_id')}<span class="mt-1 block text-xs text-error">{fe('location_id')}</span>{/if}
		<a class="link mt-1 inline-flex items-center gap-1 text-xs" href="/calendar/locations" target="_blank" rel="noopener">{t('eventform.manage_locations')} <ExternalLink size={11} aria-hidden="true" /></a>
	</div>

	<!-- Who -->
	{@render section(t('eventform.section.who'), Users)}
	<EventFormAudience bind:s={v} {options} fields={shown} />

	<div class="mt-8 grid gap-4 border-t border-rule pt-5">
		<label class="flex cursor-pointer items-start gap-3">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={v.notify} />
			<span class="grid">
				<span class="label-text">{mode === 'create' ? t('eventform.notify_create') : t('eventform.notify_edit')}</span>
				<span class="text-xs text-muted">{mode === 'create' ? t('eventform.notify_create_help') : t('eventform.notify_edit_help')}</span>
			</span>
		</label>
		{#if fe('scope')}<span class="block text-xs text-error">{fe('scope')}</span>{/if}
		<div class="flex flex-wrap items-center gap-2">
			<a class="btn btn-outline gap-2" href={cancelHref}><ArrowLeft size={16} /> {t('common.cancel')}</a>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Save size={16} /> {busy ? t('eventform.saving') : mode === 'create' ? t('eventform.create') : t('eventform.save')}</button>
			{#if ondelete}
				<button class="btn btn-outline btn-error ml-auto gap-2" type="button" onclick={ondelete}><Trash2 size={16} /> {t('common.delete')}</button>
			{/if}
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
