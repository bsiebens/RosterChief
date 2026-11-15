<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { ArrowLeft, Check, CircleHelp, Minus, RotateCcw, Shirt, X } from '@lucide/svelte';
	import { remind, isGame } from '#lib/coach.ts';
	import { formatDateTime, type AttendanceRow, type BulkAnswerOut } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';

	type Answer = 'present' | 'maybe' | 'absent';
	let { data } = $props();
	let mode = $state<'answers' | 'checkin'>('answers');
	let filter = $state<'all' | 'in' | 'out' | 'unknown'>('all');
	let query = $state('');
	let edits = $state<Record<string, Answer>>({});
	let busy = $state('');
	let saving = $state(false);
	let message = $state('');
	let failed = $state(false);

	const answerOf = (row: AttendanceRow): Answer | '' => (['present', 'selected', 'not_selected'].includes(row.status) ? 'present' : ['absent', 'excused', 'dropout'].includes(row.status) ? 'absent' : row.status === 'maybe' ? 'maybe' : '');
	const current = (row: AttendanceRow): Answer | '' => edits[row.member.id] ?? answerOf(row);
	const bucket = (row: AttendanceRow) => (current(row) === 'present' ? 'in' : current(row) === 'absent' ? 'out' : 'unknown');
	const counts = $derived({ all: data.attendance.rows.length, in: data.attendance.rows.filter((row) => bucket(row) === 'in').length, out: data.attendance.rows.filter((row) => bucket(row) === 'out').length, unknown: data.attendance.rows.filter((row) => bucket(row) === 'unknown').length });
	const rows = $derived(data.attendance.rows.filter((row) => (filter === 'all' || bucket(row) === filter) && (!query.trim() || `${row.last_name} ${row.first_name} ${row.first_name} ${row.last_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))));
	const changed = $derived(data.attendance.rows.filter((row) => edits[row.member.id] && edits[row.member.id] !== answerOf(row)));
	const upcoming = $derived(!data.event.cancelled && Date.parse(data.event.start) > Date.now());
	const when = $derived([formatDateTime(data.event.start), data.event.location?.name].filter(Boolean).join(' · '));
	usePageHeader(() => ({ title: data.event.title, context: when }));

	const SEGMENTS = [['all', 'portal.all'], ['in', 'coach.seg_in'], ['out', 'coach.seg_out'], ['unknown', '']] as const;
	const OPTIONS: { answer: Answer; label: string; on: string }[] = [
		{ answer: 'present', label: 'coach.answer_in', on: 'bg-ok border-ok text-[#fff]' },
		{ answer: 'maybe', label: 'coach.answer_maybe', on: 'bg-warn border-warn text-[#1b1400]' },
		{ answer: 'absent', label: 'coach.answer_out', on: 'bg-club border-club text-club-content' }
	];
	const subline = (row: AttendanceRow) => {
		const base = [row.jersey_number != null ? `#${row.jersey_number}` : '', row.position].filter(Boolean).join(' · ');
		if (current(row) === 'absent') return [t('coach.seg_out'), row.note].filter(Boolean).join(' · ');
		return current(row) === '' ? [base, t('coach.no_reply_note')].filter(Boolean).join(' · ') : base;
	};

	async function save() {
		if (saving || !changed.length) return;
		saving = true;
		message = '';
		failed = false;
		const refused: string[] = [];
		for (const answer of ['present', 'maybe', 'absent'] as Answer[]) {
			const ids = changed.filter((row) => edits[row.member.id] === answer).map((row) => row.member.id);
			if (!ids.length) continue;
			const response = await api<BulkAnswerOut>('POST', `/events/${data.event.id}/attendance/bulk`, { body: { member_ids: ids, status: answer } });
			if (!response.ok || !response.data) {
				failed = true;
				message = response.message ?? t('common.error.generic');
				continue;
			}
			for (const result of response.data.results) {
				if (result.ok) delete edits[result.member_id];
				else refused.push(`${nameOf(result.member_id)}${result.error ? ` (${result.error})` : ''}`);
			}
		}
		await invalidateAll();
		if (refused.length) {
			failed = true;
			message = t('coach.save_partial', { names: refused.join(', ') });
		} else if (!failed) message = t('coach.attendance_saved');
		saving = false;
	}
	const nameOf = (id: string) => {
		const row = data.attendance.rows.find((entry) => entry.member.id === id);
		return row ? `${row.last_name}, ${row.first_name}` : '';
	};

	async function checkIn(row: AttendanceRow, showedUp: boolean | null) {
		if (busy) return;
		busy = row.member.id;
		message = '';
		failed = false;
		const response = await api('PUT', `/events/${data.event.id}/attendance/${row.member.id}/check-in`, { body: { showed_up: showedUp } });
		if (response.ok) await invalidateAll();
		else {
			failed = true;
			message = response.message ?? t('common.error.generic');
		}
		busy = '';
	}

	async function nudge() {
		busy = 'remind';
		const result = await remind(data.event.id);
		failed = !result.ok;
		message = result.ok ? t(result.count ? 'coach.reminded' : 'coach.remind_none', { n: result.count }) : result.message || t('common.error.generic');
		busy = '';
	}
</script>

<svelte:head><title>{pageTitle(data.event.title)}</title></svelte:head>

<div class="grid w-full max-w-4xl min-w-0 gap-3">
	<div class="flex items-center justify-between gap-2">
		<a class="btn btn-outline min-h-11 gap-2" href="/coach?tab=calendar"><ArrowLeft size={16} aria-hidden="true" />{t('shell.nav.events')}</a>
		<a class="link-more" href="/calendar/events/{data.event.id}?view=coach">{t('coach.event_details')} →</a>
	</div>

	<p class="text-sm text-muted sm:hidden">{when}</p>
	<section class="card overflow-hidden">
		<div class="flex flex-wrap items-center gap-2 border-b border-line px-3.5 py-3">
			<span class="inline-flex gap-1" role="group" aria-label={t('events.tab.attendance')}>
				{#each SEGMENTS as [key, label] (key)}
					<button type="button" class="portal-person compact text !px-3" aria-pressed={filter === key} onclick={() => (filter = key)}>{key === 'unknown' ? '?' : t(label)} {counts[key]}</button>
				{/each}
			</span>
			<span class="flex-1"></span>
			<button type="button" class="portal-person compact text" aria-pressed={mode === 'checkin'} onclick={() => (mode = mode === 'checkin' ? 'answers' : 'checkin')}>{t('coach.check_in_mode')} {data.attendance.checked_in}/{counts.in}</button>
			{#if upcoming}<button type="button" class="btn btn-outline btn-sm min-h-9" disabled={busy === 'remind' || counts.unknown === 0} onclick={nudge}>{t('coach.remind')}</button>{/if}
		</div>
		<div class="border-b border-line px-3.5 py-2">
			<label class="sr-only" for="att-search">{t('portal.search_players')}</label>
			<input id="att-search" type="search" class="input w-full sm:max-w-xs" placeholder={t('portal.search_players')} bind:value={query} />
		</div>
		{#if message}<p class="border-b border-line px-3.5 py-2 text-sm {failed ? 'text-danger' : 'text-ok-text'}" role={failed ? 'alert' : 'status'}>{message}</p>{/if}
		<ul class="divide-y divide-rule">
			{#each rows as row (row.member.id)}
				{@const answer = current(row)}
				<li class="flex min-h-14 items-center gap-3 px-3.5 py-2">
					<Avatar first_name={row.first_name} last_name={row.last_name} size="size-9" text="text-xs" />
					<div class="min-w-0 flex-1">
						<p class="truncate text-sm font-semibold text-ink">{row.last_name}, {row.first_name}</p>
						<p class="truncate text-xs {answer === 'absent' ? 'text-club-dark' : 'text-muted'}">{subline(row)}{#if mode === 'answers' && row.showed_up !== null} · {t(row.showed_up ? 'events.att.checked_in' : 'events.att.no_show')}{/if}</p>
					</div>
					{#if mode === 'answers'}
						<div class="inline-flex shrink-0 gap-1" role="group" aria-label={row.last_name + ', ' + row.first_name}>
							{#each OPTIONS as option (option.answer)}
								<button type="button" class="grid size-10 place-items-center rounded-lg border {answer === option.answer ? option.on : 'border-line bg-(--portal-panel) text-muted hover:text-ink'}" aria-pressed={answer === option.answer} aria-label={t(option.label) + ' · ' + row.last_name} onclick={() => (edits[row.member.id] = option.answer)}>
									{#if option.answer === 'present'}<Check size={18} aria-hidden="true" />{:else if option.answer === 'maybe'}<CircleHelp size={18} aria-hidden="true" />{:else}<Minus size={18} aria-hidden="true" />{/if}
								</button>
							{/each}
						</div>
					{:else}
						<div class="inline-flex shrink-0 gap-1">
							<button type="button" class="grid size-10 place-items-center rounded-lg border {row.showed_up === true ? 'border-ok bg-ok text-[#fff]' : 'border-line bg-(--portal-panel) text-muted'}" disabled={!!busy} aria-pressed={row.showed_up === true} aria-label={t('portal.check_in') + ' · ' + row.member.name} onclick={() => checkIn(row, true)}><Check size={18} aria-hidden="true" /></button>
							<button type="button" class="grid size-10 place-items-center rounded-lg border {row.showed_up === false ? 'border-club bg-club text-club-content' : 'border-line bg-(--portal-panel) text-muted'}" disabled={!!busy} aria-pressed={row.showed_up === false} aria-label={t('events.att.no_show') + ' · ' + row.member.name} onclick={() => checkIn(row, false)}><X size={18} aria-hidden="true" /></button>
							<button type="button" class="grid size-10 place-items-center rounded-lg border border-line bg-(--portal-panel) text-muted disabled:opacity-40" disabled={!!busy || row.showed_up === null} aria-label={t('portal.clear_check_in') + ' · ' + row.member.name} onclick={() => checkIn(row, null)}><RotateCcw size={16} aria-hidden="true" /></button>
						</div>
					{/if}
				</li>
			{:else}
				<li class="px-4 py-3 text-sm text-muted">{t('portal.no_players')}</li>
			{/each}
		</ul>
	</section>

	<div class="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-10 flex items-center gap-2 rounded-box border border-line bg-(--portal-panel) p-2.5 shadow-sm md:bottom-2">
		{#if isGame(data.event)}<a class="btn btn-outline min-h-12 flex-1 gap-2" href="/calendar/events/{data.event.id}?tab=lineup&view=coach"><Shirt size={16} aria-hidden="true" />{t('portal.lineup')}</a>{/if}
		<button type="button" class="btn btn-primary min-h-12 flex-1" disabled={saving || !changed.length} onclick={save}>{t('coach.save_attendance')}{#if changed.length} ({changed.length}){/if}</button>
	</div>
</div>
