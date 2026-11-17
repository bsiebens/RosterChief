<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { Check, CircleHelp, Minus, RotateCcw, X } from '@lucide/svelte';
	import { remind } from '#lib/coach.ts';
	import type { AttendanceList, AttendanceRow, BulkAnswerOut } from '#lib/events/types.ts';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import Toast from '#lib/components/Toast.svelte';

	type Answer = 'present' | 'maybe' | 'absent';
	/** The coach's attendance screen: replies and check-in, saved together. Shown in the event page's Attendance tab on phones. */
	let { event, list }: { event: { id: string; start: string; cancelled: boolean }; list: AttendanceList } = $props();
	let mode = $state<'answers' | 'checkin'>('answers');
	let filter = $state<'all' | 'in' | 'out' | 'unknown'>('all');
	let query = $state('');
	let edits = $state<Record<string, Answer>>({});
	// The coach's own note about an absence: staff only, never shown to the player or their family.
	let notes = $state<Record<string, string>>({});
	let noting = $state<AttendanceRow | null>(null);
	let draft = $state('');
	const noteOf = (row: AttendanceRow) => notes[row.member.id] ?? row.coach_note ?? '';
	function chooseOut(row: AttendanceRow) {
		noting = row;
		draft = noteOf(row);
	}
	function confirmOut() {
		if (!noting) return;
		edits[noting.member.id] = 'absent';
		notes[noting.member.id] = draft.trim();
		noting = null;
	}
	let busy = $state('');
	// Check-ins are set here and saved together with the answers; they stay until the refreshed data arrives.
	let checks = $state<Record<string, boolean | null>>({});
	const shownUp = (row: AttendanceRow) => (row.member.id in checks ? checks[row.member.id] : row.showed_up);
	let saving = $state(false);
	let message = $state('');
	let failed = $state(false);

	const answerOf = (row: AttendanceRow): Answer | '' => (['present', 'selected', 'not_selected'].includes(row.status) ? 'present' : ['absent', 'excused', 'dropout'].includes(row.status) ? 'absent' : row.status === 'maybe' ? 'maybe' : '');
	const current = (row: AttendanceRow): Answer | '' => edits[row.member.id] ?? answerOf(row);
	const bucket = (row: AttendanceRow) => (current(row) === 'present' ? 'in' : current(row) === 'absent' ? 'out' : 'unknown');
	const counts = $derived({ all: list.rows.length, in: list.rows.filter((row) => bucket(row) === 'in').length, out: list.rows.filter((row) => bucket(row) === 'out').length, unknown: list.rows.filter((row) => bucket(row) === 'unknown').length });
	const rows = $derived(list.rows.filter((row) => (filter === 'all' || bucket(row) === filter) && (!query.trim() || `${row.last_name} ${row.first_name} ${row.first_name} ${row.last_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))));
	const checkChanged = $derived(list.rows.filter((row) => row.member.id in checks && checks[row.member.id] !== row.showed_up));
	const changed = $derived(list.rows.filter((row) => (edits[row.member.id] && edits[row.member.id] !== answerOf(row)) || (notes[row.member.id] !== undefined && notes[row.member.id] !== (row.coach_note ?? ''))));
	const pending = $derived(new Set([...changed.map((row) => row.member.id), ...checkChanged.map((row) => row.member.id)]).size);
	const upcoming = $derived(!event.cancelled && Date.parse(event.start) > Date.now());

	const SEGMENTS = [['all', 'portal.all'], ['in', 'coach.seg_in'], ['out', 'coach.seg_out'], ['unknown', '']] as const;
	const OPTIONS: { answer: Answer; label: string; on: string }[] = [
		{ answer: 'present', label: 'coach.answer_in', on: 'bg-ok border-ok text-[#fff]' },
		{ answer: 'maybe', label: 'coach.answer_maybe', on: 'bg-warn border-warn text-[#1b1400]' },
		{ answer: 'absent', label: 'coach.answer_out', on: 'bg-club border-club text-club-content' }
	];
	const subline = (row: AttendanceRow) => {
		const base = [row.position].filter(Boolean).join(' · ');
		if (current(row) === 'absent') return [t('coach.seg_out'), row.note, noteOf(row)].filter(Boolean).join(' · ');
		return current(row) === '' ? [base, t('coach.no_reply_note')].filter(Boolean).join(' · ') : base;
	};

	async function save() {
		if (saving || !pending) return;
		saving = true;
		message = '';
		failed = false;
		const refused: string[] = [];
		// What was saved keeps showing as edited until the refreshed data is in, so the old answer never flashes back.
		const saved: string[] = [];
		const savedChecks: string[] = [];
		// Absences carry the coach's note, so they go one by one; the others in one call per answer.
		for (const row of changed.filter((entry) => current(entry) === 'absent')) {
			const response = await api('PUT', `/events/${event.id}/attendance/${row.member.id}`, { body: { status: edits[row.member.id] === 'absent' && answerOf(row) !== 'absent' ? 'absent' : null, coach_note: noteOf(row) } });
			if (response.ok) saved.push(row.member.id);
			else refused.push(`${nameOf(row.member.id)}${response.message ? ` (${response.message})` : ''}`);
		}
		for (const answer of ['present', 'maybe'] as Answer[]) {
			const ids = changed.filter((row) => edits[row.member.id] === answer).map((row) => row.member.id);
			if (!ids.length) continue;
			const response = await api<BulkAnswerOut>('POST', `/events/${event.id}/attendance/bulk`, { body: { member_ids: ids, status: answer } });
			if (!response.ok || !response.data) {
				failed = true;
				message = response.message ?? t('common.error.generic');
				continue;
			}
			for (const result of response.data.results) {
				if (result.ok) saved.push(result.member_id);
				else refused.push(`${nameOf(result.member_id)}${result.error ? ` (${result.error})` : ''}`);
			}
		}
		for (const row of checkChanged) {
			const response = await api('PUT', `/events/${event.id}/attendance/${row.member.id}/check-in`, { body: { showed_up: checks[row.member.id] } });
			if (response.ok) savedChecks.push(row.member.id);
			else refused.push(`${nameOf(row.member.id)}${response.message ? ` (${response.message})` : ''}`);
		}
		await invalidateAll();
		for (const id of savedChecks) delete checks[id];
		for (const id of saved) {
			delete edits[id];
			delete notes[id];
		}
		if (refused.length) {
			failed = true;
			message = t('coach.save_partial', { names: refused.join(', ') });
		} else if (!failed) message = t('coach.attendance_saved');
		saving = false;
	}
	const nameOf = (id: string) => {
		const row = list.rows.find((entry) => entry.member.id === id);
		return row ? `${row.last_name}, ${row.first_name}` : '';
	};

	const checkIn = (row: AttendanceRow, showedUp: boolean | null) => {
		checks[row.member.id] = showedUp;
	};

	async function nudge() {
		busy = 'remind';
		const result = await remind(event.id);
		failed = !result.ok;
		message = result.ok ? t(result.count ? 'coach.reminded' : 'coach.remind_none', { n: result.count }) : result.message || t('common.error.generic');
		busy = '';
	}
</script>

<div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3">
	<section class="card overflow-hidden">
		<div class="grid gap-3 border-b border-line px-3.5 py-3">
			<div class="grid grid-cols-2 gap-2" role="group" aria-label={t('events.tab.attendance')}>
				{#each [['answers', t('coach.mode_replies')], ['checkin', `${t('coach.check_in_mode')} ${list.checked_in}/${counts.in}`]] as [key, label] (key)}
					<button type="button" class="min-h-11 rounded-full border px-3 font-display text-sm font-bold tracking-wide uppercase {mode === key ? 'border-ink bg-ink text-paper' : 'border-line bg-(--portal-panel) text-ink'}" aria-pressed={mode === key} onclick={() => (mode = key as typeof mode)}>{label}</button>
				{/each}
			</div>
			<div class="flex items-center gap-2">
				<div class="flex min-w-0 flex-1 gap-1.5" role="group" aria-label={t('portal.all')}>
					{#each SEGMENTS as [key, label] (key)}
						<button type="button" class="inline-flex h-[38px] min-w-0 flex-1 items-center justify-center gap-1 rounded-full border px-1.5 text-[14px] font-semibold {filter === key ? 'border-ink bg-ink text-paper' : 'border-line bg-(--portal-panel) text-ink'}" aria-pressed={filter === key} onclick={() => (filter = key)}>{key === 'unknown' ? t('coach.waiting') : t(label)} <span class="tabular-nums {filter === key ? '' : 'text-muted'}">{counts[key]}</span></button>
					{/each}
				</div>
			</div>
		</div>
		<div class="border-b border-line px-3.5 py-2">
			<label class="sr-only" for="att-search">{t('portal.search_players')}</label>
			<input id="att-search" type="search" class="input w-full sm:max-w-xs" placeholder={t('portal.search_players')} bind:value={query} />
		</div>
		<ul class="divide-y divide-rule">
			{#each rows as row (row.member.id)}
				{@const answer = current(row)}
				<li class="flex min-h-14 items-center gap-3 px-3.5 py-2">
					<span class="grid size-7 shrink-0 place-items-center rounded-full {answer === 'present' ? 'bg-ok text-[#fff]' : answer === 'absent' ? 'bg-club text-club-content' : answer === 'maybe' ? 'bg-warn text-[#1b1400]' : 'border-2 border-line text-transparent'}" aria-hidden="true">{#if answer === 'present'}<Check size={15} />{:else if answer === 'absent'}<Minus size={15} />{:else if answer === 'maybe'}<CircleHelp size={15} />{/if}</span>
					<span class="grid size-9 shrink-0 place-items-center rounded-box bg-subhead font-display text-base font-extrabold text-ink">{row.jersey_number ?? '–'}</span>
					<div class="min-w-0 flex-1">
						<p class="truncate text-base font-semibold text-ink">{row.first_name} {row.last_name}</p>
						<p class="truncate text-sm {answer === 'absent' ? 'text-club-dark' : 'text-muted'}">{subline(row)}{#if mode === 'answers' && shownUp(row) !== null} · {t(shownUp(row) ? 'events.att.checked_in' : 'events.att.no_show')}{/if}</p>
					</div>
					{#if mode === 'answers'}
						<div class="inline-flex shrink-0 gap-1" role="group" aria-label={row.last_name + ', ' + row.first_name}>
							{#each OPTIONS as option (option.answer)}
								<button type="button" class="grid size-10 place-items-center rounded-lg border {answer === option.answer ? option.on : 'border-line bg-(--portal-panel) text-muted hover:text-ink'}" aria-pressed={answer === option.answer} aria-label={t(option.label) + ' · ' + row.last_name} onclick={() => (option.answer === 'absent' ? chooseOut(row) : (edits[row.member.id] = option.answer))}>
									{#if option.answer === 'present'}<Check size={18} aria-hidden="true" />{:else if option.answer === 'maybe'}<CircleHelp size={18} aria-hidden="true" />{:else}<Minus size={18} aria-hidden="true" />{/if}
								</button>
							{/each}
						</div>
					{:else}
						<div class="inline-flex shrink-0 gap-1">
							<button type="button" class="grid size-10 place-items-center rounded-lg border {shownUp(row) === true ? 'border-ok bg-ok text-[#fff]' : 'border-line bg-(--portal-panel) text-muted'}" aria-pressed={shownUp(row) === true} aria-label={t('portal.check_in') + ' · ' + row.member.name} onclick={() => checkIn(row, true)}><Check size={18} aria-hidden="true" /></button>
							<button type="button" class="grid size-10 place-items-center rounded-lg border {shownUp(row) === false ? 'border-club bg-club text-club-content' : 'border-line bg-(--portal-panel) text-muted'}" aria-pressed={shownUp(row) === false} aria-label={t('events.att.no_show') + ' · ' + row.member.name} onclick={() => checkIn(row, false)}><X size={18} aria-hidden="true" /></button>
							<button type="button" class="grid size-10 place-items-center rounded-lg border border-line bg-(--portal-panel) text-muted disabled:opacity-40" disabled={shownUp(row) === null} aria-label={t('portal.clear_check_in') + ' · ' + row.member.name} onclick={() => checkIn(row, null)}><RotateCcw size={16} aria-hidden="true" /></button>
						</div>
					{/if}
				</li>
			{:else}
				<li class="px-4 py-3 text-sm text-muted">{t('portal.no_players')}</li>
			{/each}
		</ul>
	</section>

	<div class="action-dock md:sticky md:bottom-2 md:z-10 md:rounded-box md:border md:border-line md:bg-(--portal-panel) md:p-2.5 md:shadow-sm"><div class="flex w-full items-center gap-2">
		{#if mode === 'answers' && upcoming}<button type="button" class="btn btn-primary min-h-12 flex-1" disabled={busy === 'remind' || counts.unknown === 0} onclick={nudge}>{t('coach.remind_n', { n: counts.unknown })}</button>{/if}
		<button type="button" class="btn {mode === 'answers' && upcoming ? 'btn-outline' : 'btn-primary'} min-h-12 flex-1" disabled={saving || !pending} onclick={save}>{t('coach.save_attendance')}{#if pending} ({pending}){/if}</button>
	</div></div>
	<div class="h-[4.75rem] md:hidden" aria-hidden="true"></div>
</div>

<Toast {message} error={failed} aboveBand onhide={() => (message = '')} />

<Sheet open={!!noting} title={noting ? `${noting.last_name}, ${noting.first_name}` : ''} onclose={() => (noting = null)}>
	{#if noting}
		<form class="grid gap-4 p-4 pt-0" onsubmit={(e) => { e.preventDefault(); confirmOut(); }}>
			<div>
				<h2 class="font-display text-xl font-extrabold text-ink uppercase">{t('coach.out_title', { name: noting.first_name })}</h2>
				<p class="text-xs text-muted">{noting.last_name}, {noting.first_name}</p>
			</div>
			{#if noting.note}<p class="rounded-box border border-line bg-subhead px-3 py-2 text-sm"><span class="block text-xs font-semibold text-muted">{t('coach.player_reason')}</span>{noting.note}</p>{/if}
			<label class="grid gap-1 text-sm font-semibold">{t('coach.coach_note')}
				<textarea class="textarea textarea-bordered w-full font-normal" rows="4" maxlength="500" bind:value={draft} placeholder={t('coach.coach_note_placeholder')}></textarea>
				<span class="text-xs font-normal text-muted">{t('coach.coach_note_help')}</span>
			</label>
			<div class="flex flex-wrap justify-end gap-2">
				<button type="button" class="btn btn-outline" onclick={() => (noting = null)}>{t('common.cancel')}</button>
				<button type="submit" class="btn btn-primary">{t('coach.mark_out')}</button>
			</div>
		</form>
	{/if}
</Sheet>
