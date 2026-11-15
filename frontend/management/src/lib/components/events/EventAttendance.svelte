<script lang="ts">
    import {Check, CircleHelp, History, Lock, NotebookPen, UserCheck, UserX, X, TriangleAlert} from '@lucide/svelte';
    import Alert from '#lib/components/Alert.svelte';
    import Avatar from '#lib/components/Avatar.svelte';
    import Modal from '#lib/components/Modal.svelte';
    import {t} from '#lib/i18n.svelte.ts';
    import {formatDateTime} from '#lib/members/detail-helpers.ts';
    import {api, type ApiCall} from '#lib/management-api.ts';
    import {ATTENDANCE_STATUSES, type AttendanceChange, type AttendanceCounts, type AttendanceList, type AttendanceRow, type BulkAnswerOut, COACH_ANSWERS, statusLabelKey, statusStyle} from '#lib/events/types.ts';

    /**
     * The Attendance tab: counts, the invited members and (for attendance managers) the coach controls. Writes go from
     * the browser through the /api/v1 proxy; the row is changed at once and replaced by the server's answer (or put
     * back, with an error, when the server refuses).
     */
    let {eventId, initial, past, cancelled}: { eventId: string; initial: AttendanceList; past: boolean; cancelled: boolean } = $props();

    // svelte-ignore state_referenced_locally
    let list = $state<AttendanceList>($state.snapshot(initial));
    $effect(() => {
        list = $state.snapshot(initial);
    });

    const manage = $derived(list.can_manage && !cancelled);
    let filter = $state<string>('all');
    let selected = $state<string[]>([]);
    let notify = $state(false);
    let errorMsg = $state('');
    let bulkMsg = $state('');
    let busy = $state<Record<string, boolean>>({});
    let bulkBusy = $state(false);

    const failText = (res: ApiCall<unknown>) => res.fields.__all__ || res.fields.status || res.fields.coach_note || res.message || t('common.error.generic');

    function recount(rows: AttendanceRow[]): AttendanceCounts {
        const c: AttendanceCounts = {present: 0, absent: 0, excused: 0, maybe: 0, no_response: 0, selected: 0, not_selected: 0, total: 0};
        for (const r of rows) {
            if (r.status in c) c[r.status as keyof AttendanceCounts] += 1;
            c.total += 1;
        }
        return c;
    }

    function replaceRow(row: AttendanceRow) {
        list.rows = list.rows.map((r) => (r.member.id === row.member.id ? row : r));
        list.counts = recount(list.rows);
        list.checked_in = list.rows.filter((r) => r.showed_up === true).length;
        list.no_shows = list.rows.filter((r) => r.showed_up === false && (r.status === 'present' || r.status === 'selected')).length;
    }

    async function reload() {
        const res = await api<AttendanceList>('GET', `/events/${eventId}/attendance`);
        if (res.ok && res.data) list = res.data;
    }

    const shownStatuses = $derived(ATTENDANCE_STATUSES.filter((s) => ['present', 'absent', 'maybe', 'no_response'].includes(s) || list.counts[s] > 0));
    const rows = $derived(filter === 'all' ? list.rows : list.rows.filter((r) => r.status === filter));
    const allShownSelected = $derived(rows.length > 0 && rows.every((r) => selected.includes(r.member.id)));

    async function setStatus(row: AttendanceRow, status: string) {
        if (status === row.status) return;
        errorMsg = '';
        const before = {...row};
        replaceRow({...row, status});
        busy[row.member.id] = true;
        const res = await api<AttendanceRow>('PUT', `/events/${eventId}/attendance/${row.member.id}`, {body: {status, notify}});
        busy[row.member.id] = false;
        if (res.ok && res.data) replaceRow(res.data);
        else {
            replaceRow(before);
            errorMsg = t('events.att.failed_named', {name: row.member.name, error: failText(res)});
        }
    }

    async function checkIn(row: AttendanceRow, showed_up: boolean | null) {
        errorMsg = '';
        const before = {...row};
        replaceRow({...row, showed_up});
        busy[row.member.id] = true;
        const res = await api<AttendanceRow>('PUT', `/events/${eventId}/attendance/${row.member.id}/check-in`, {body: {showed_up}});
        busy[row.member.id] = false;
        if (res.ok && res.data) replaceRow(res.data);
        else {
            replaceRow(before);
            errorMsg = t('events.att.failed_named', {name: row.member.name, error: failText(res)});
        }
    }

    async function bulk(status: 'present' | 'absent') {
        if (!selected.length) return;
        errorMsg = '';
        bulkMsg = '';
        bulkBusy = true;
        const ids = [...selected];
        for (const r of list.rows) if (ids.includes(r.member.id) && r.status !== 'selected') replaceRow({...r, status});
        const res = await api<BulkAnswerOut>('POST', `/events/${eventId}/attendance/bulk`, {body: {member_ids: ids, status, notify}});
        await reload();
        bulkBusy = false;
        if (!res.ok || !res.data) {
            errorMsg = failText(res);
            return;
        }
        const failed = res.data.results.filter((r) => !r.ok);
        if (failed.length) {
            const name = (id: string) => list.rows.find((r) => r.member.id === id)?.member.name ?? id;
            errorMsg = t('events.att.bulk_partial', {n: failed.length}) + ' ' + failed.map((f) => `${name(f.member_id)}: ${f.error}`).join('; ');
        } else bulkMsg = t('events.att.bulk_done', {n: res.data.results.length});
        selected = [];
    }

    // Coach note editor. The API only takes the note together with an answer, so the row's own answer is sent along
    // (line-up picks are kept by "present"); excused rows cannot be given one (see report).
    let noteOpen = $state(false);
    let historyOpen = $state(false);
    let historyRow = $state<AttendanceRow | null>(null);
    let history = $state<AttendanceChange[]>([]);
    let historyBusy = $state(false);
    let historyError = $state('');

    async function openHistory(r: AttendanceRow) {
        historyRow = r;
        history = [];
        historyError = '';
        historyBusy = true;
        historyOpen = true;
        const res = await api<AttendanceChange[]>('GET', `/events/${eventId}/attendance/${r.member.id}/history`);
        historyBusy = false;
        if (res.ok && res.data) history = res.data;
        else historyError = res.message || t('common.error.generic');
    }

    /** Who set the shown answer: a parent is shown "Name (for Child)". */
    function answeredBy(r: AttendanceRow): string {
        const name = r.answered_by_name;
        if (!name) return r.answered_by_coach ? t('events.att.by_coach_short') : '';
        return r.answered_via === 'guardian' ? t('events.att.by_guardian', {name, child: r.first_name}) : name;
    }

    function changeBy(c: AttendanceChange): string {
        const name = c.changed_by_name || t('events.att.history_unknown');
        return c.via === 'guardian' ? t('events.att.by_guardian', {name, child: historyRow?.first_name ?? ''}) : name;
    }

    let noteRow = $state<AttendanceRow | null>(null);
    let noteText = $state('');
    let noteError = $state('');
    let noteBusy = $state(false);

    function openNote(r: AttendanceRow) {
        noteRow = r;
        noteText = r.coach_note ?? '';
        noteError = '';
        noteOpen = true;
    }

    async function saveNote(e: SubmitEvent) {
        e.preventDefault();
        if (!noteRow) return;
        noteBusy = true;
        noteError = '';
        const res = await api<AttendanceRow>('PUT', `/events/${eventId}/attendance/${noteRow.member.id}`, {body: {coach_note: noteText.trim(), notify: false}});
        noteBusy = false;
        if (res.ok && res.data) {
            replaceRow(res.data);
            noteOpen = false;
        } else noteError = failText(res);
    }

    function toggleAll() {
        const ids = rows.map((r) => r.member.id);
        selected = allShownSelected ? selected.filter((id) => !ids.includes(id)) : [...new Set([...selected, ...ids])];
    }
</script>

<div class="grid gap-4">
    <!-- Counts strip. -->
    <div class="card flex flex-wrap divide-x divide-rule">
        {#each shownStatuses as s (s)}
            <div class="min-w-24 flex-1 px-4 py-3">
                <span class="block font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t(statusLabelKey(s))}</span>
                <span class="font-display text-3xl leading-none font-extrabold text-ink">{list.counts[s]}</span>
            </div>
        {/each}
        <div class="min-w-24 flex-1 px-4 py-3">
            <span class="block font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('events.att.invited')}</span>
            <span class="font-display text-3xl leading-none font-extrabold text-ink">{list.counts.total}</span>
        </div>
        {#if past}
            <div class="min-w-24 flex-1 px-4 py-3">
                <span class="block font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('events.att.checked_in')}</span>
                <span class="font-display text-3xl leading-none font-extrabold text-ok-text">{list.checked_in}</span>
            </div>
            <div class="min-w-24 flex-1 px-4 py-3">
                <span class="block font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('events.att.no_shows')}</span>
                <span class="font-display text-3xl leading-none font-extrabold text-danger">{list.no_shows}</span>
            </div>
        {/if}
    </div>

    {#if errorMsg}
        <Alert kind="error">{errorMsg}</Alert>
    {/if}
    {#if bulkMsg}
        <Alert kind="success">{bulkMsg}</Alert>
    {/if}
    {#if list.can_manage && cancelled}
        <Alert kind="info">{t('events.att.cancelled_locked')}</Alert>
    {/if}

    <div class="card">
        <div class="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
            <div class="flex flex-wrap gap-1" role="group" aria-label={t('events.att.filter')}>
                <button type="button" class="btn btn-xs {filter === 'all' ? 'btn-primary' : 'btn-outline'}" aria-pressed={filter === 'all'} onclick={() => (filter = 'all')}>{t('events.att.all')} · {list.counts.total}</button>
                {#each shownStatuses as s (s)}
                    <button type="button" class="btn btn-xs {filter === s ? 'btn-primary' : 'btn-outline'}" aria-pressed={filter === s} onclick={() => (filter = s)}>{t(statusLabelKey(s))} · {list.counts[s]}</button>
                {/each}
            </div>
            <div class="flex-1"></div>
            {#if manage}
                <label class="flex cursor-pointer items-center gap-2 text-sm text-slate">
                    <input type="checkbox" class="checkbox" bind:checked={notify}/>
                    {t('events.att.notify')}
                </label>
            {/if}
        </div>
        {#if manage && selected.length}
            <div class="flex flex-wrap items-center gap-2 border-b border-line bg-row-sel px-4 py-2" role="region" aria-label={t('events.att.bulk')}>
                <span class="text-sm font-semibold text-ink">{t('events.att.n_selected', {n: selected.length})}</span>
                <button type="button" class="btn btn-success btn-xs gap-1" disabled={bulkBusy} onclick={() => bulk('present')}>
                    <Check size={13}/> {t('events.att.mark_present')}</button>
                <button type="button" class="btn btn-error btn-xs gap-1" disabled={bulkBusy} onclick={() => bulk('absent')}>
                    <X size={13}/> {t('events.att.mark_absent')}</button>
                <button type="button" class="btn btn-ghost btn-xs" onclick={() => (selected = [])}>{t('common.clear')}</button>
            </div>
        {/if}
        <div class="overflow-x-auto">
            <table class="table">
                <thead>
                    <tr>
                        {#if manage}
                            <th class="w-8"><input type="checkbox" class="checkbox" aria-label={t('events.att.select_all')} checked={allShownSelected} onchange={toggleAll}/></th>
                        {/if}
                        <th>{t('events.att.col.member')}</th>
                        <th>{t('events.att.col.position')}</th>
                        <th>{t('events.att.col.status')}</th>
                        {#if past}
                            <th>{t('events.att.col.showed_up')}</th>
                        {/if}
                        <th>{t('events.att.col.note')}</th>
                        {#if manage}
                            <th>{t('events.att.col.coach_note')}</th>
                        {/if}
                    </tr>
                </thead>
                <tbody>
                    {#each rows as r (r.member.id)}
                        <tr class="hover:bg-paper {selected.includes(r.member.id) ? 'bg-row-sel' : ''}">
                            {#if manage}
                                <td><input type="checkbox" class="checkbox" aria-label={t('events.att.select_named', { name: r.member.name })} bind:group={selected} value={r.member.id}/></td>
                            {/if}
                            <td>
                                <div class="flex items-center gap-3">
                                    <Avatar first_name={r.first_name} last_name={r.last_name}/>
                                    <div class="min-w-0">
                                        <a class="link font-semibold" href="/members/{r.member.id}">{r.member.name}</a>
                                        {#if r.selected}<span class="badge badge-xs badge-success ml-1">{t('events.att.in_lineup')}</span>{/if}
                                    </div>
                                </div>
                            </td>
                            <td class="text-sm text-muted">
                                {#if r.jersey_number !== null}<span class="mr-1 font-display font-extrabold text-ink">#{r.jersey_number}</span>{/if}{r.position || (r.jersey_number === null ? '—' : '')}
                            </td>
                            <td>
                                {#if manage}
                                    <select
                                            class="select select-sm w-auto {statusStyle(r.status)}"
                                            aria-label={t('events.att.status_named', { name: r.member.name })}
                                            value={r.status}
                                            disabled={busy[r.member.id]}
                                            onchange={(e) => setStatus(r, e.currentTarget.value)}>
                                        {#if !(COACH_ANSWERS as readonly string[]).includes(r.status)}
                                            <option value={r.status} disabled>{t(statusLabelKey(r.status))}</option>
                                        {/if}
                                        {#each COACH_ANSWERS as s (s)}
                                            <option value={s}>{t(statusLabelKey(s))}</option>
                                        {/each}
                                    </select>
                                {:else}
                                    <span class="badge badge-sm border {statusStyle(r.status)}">{t(statusLabelKey(r.status))}</span>
                                {/if}
                                {#if answeredBy(r)}<span class="ml-1 text-xs text-dim" title={t(`events.att.via.${r.answered_via ?? 'coach'}`)}>{answeredBy(r)}</span>{/if}
                                <button type="button" class="ml-1 btn btn-ghost btn-xs btn-square align-middle" aria-label={t('events.att.history')} title={t('events.att.history')} onclick={() => openHistory(r)}>
                                    <History size={13}/>
                                </button>
                            </td>
                            {#if past}
                                <td>
                                    {#if manage}
                                        <div class="flex items-center gap-1" role="group" aria-label={t('events.att.check_in_named', { name: r.member.name })}>
                                            <button type="button" class="btn btn-xs btn-square {r.showed_up === true ? 'btn-success' : 'btn-outline'}" aria-pressed={r.showed_up === true} title={t('events.att.showed')}
                                                    aria-label={t('events.att.showed')} disabled={busy[r.member.id]} onclick={() => checkIn(r, r.showed_up === true ? null : true)}>
                                                <UserCheck size={13}/>
                                            </button>
                                            <button type="button" class="btn btn-xs btn-square {r.showed_up === false ? 'btn-error' : 'btn-outline'}" aria-pressed={r.showed_up === false} title={t('events.att.no_show')}
                                                    aria-label={t('events.att.no_show')} disabled={busy[r.member.id]} onclick={() => checkIn(r, r.showed_up === false ? null : false)}>
                                                <UserX size={13}/>
                                            </button>
                                        </div>
                                    {:else if r.showed_up === true}
                                        <span class="text-ok-text" title={t('events.att.showed')}><UserCheck size={16} aria-label={t('events.att.showed')}/></span>
                                    {:else if r.showed_up === false}
                                        <span class="text-danger" title={t('events.att.no_show')}><UserX size={16} aria-label={t('events.att.no_show')}/></span>
                                    {:else}
                                        <span class="text-dim" title={t('events.att.not_checked')}><CircleHelp size={16} aria-label={t('events.att.not_checked')}/></span>
                                    {/if}
                                </td>
                            {/if}
                            <td class="max-w-[16rem] text-sm text-slate">{r.note || '—'}</td>
                            {#if manage}
                                <td class="max-w-[16rem]">
                                    <div class="flex items-start gap-1.5">
                                        {#if r.coach_note}<span class="line-clamp-2 text-sm text-slate">{r.coach_note}</span>{/if}
                                        {#if manage}
                                            <button type="button" class="btn btn-ghost btn-xs btn-square shrink-0" aria-label={t('events.att.coach_note_edit', { name: r.member.name })} title={t('events.att.coach_note_edit', { name: r.member.name })}
                                                    onclick={() => openNote(r)}>
                                                <NotebookPen size={13}/>
                                            </button>
                                        {/if}
                                    </div>
                                </td>
                            {/if}
                        </tr>
                    {:else}
                        <tr>
                            <td colspan="7" class="py-6 text-center text-muted">{filter === 'all' ? t('events.att.none') : t('events.att.none_filtered')}</td>
                        </tr>
                    {/each}
                </tbody>
            </table>
        </div>
    </div>

    {#if list.blocked?.length}
        <div class="card card-body gap-3">
            <h3 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><TriangleAlert size={16} class="text-warn-text" aria-hidden="true" /> {t('events.att.blocked_title', {n: list.blocked.length})}</h3>
            <p class="text-xs text-muted">{t('events.att.blocked_help')}</p>
            <ul class="grid gap-2">
                {#each list.blocked as b (b.member_id)}
                    <li class="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <a class="link font-semibold" href="/members/{b.member_id}?tab=documents">{b.first_name} {b.last_name}</a>
                        {#each b.requirements as name (name)}<span class="badge badge-warning badge-sm">{name}</span>{/each}
                    </li>
                {/each}
            </ul>
        </div>
    {/if}
</div>

<Modal bind:open={historyOpen} title={t('events.att.history_title')}>
    <p class="mb-3 text-sm font-semibold text-ink">{historyRow?.member.name}</p>
    {#if historyError}
        <Alert kind="error">{historyError}</Alert>
    {/if}
    {#if historyBusy}
        <span class="loading loading-spinner loading-sm" aria-hidden="true"></span>
    {:else if !historyError && history.length === 0}
        <p class="text-sm text-muted">{t('events.att.history_empty')}</p>
    {:else}
        <ul class="grid gap-2">
            {#each history as c (c.id)}
                <li class="rounded-lg border border-line p-2 text-sm">
                    <div class="flex flex-wrap items-center justify-between gap-2">
                        <span class="font-semibold text-ink">{changeBy(c)}</span>
                        <span class="font-mono text-xs whitespace-nowrap text-muted">{formatDateTime(c.created)}</span>
                    </div>
                    <div class="mt-1 flex flex-wrap items-center gap-2">
                        <span class="text-xs text-dim">{t(`events.att.via.${c.via}`)}</span>
                        <span>{t(statusLabelKey(c.from_status))} → {t(statusLabelKey(c.to_status))}</span>
                        {#if c.note_changed}<span class="text-xs text-dim">({t('events.att.note_changed')})</span>{/if}
                    </div>
                </li>
            {/each}
        </ul>
    {/if}
    <div class="modal-action mt-4">
        <button type="button" class="btn btn-outline" onclick={() => (historyOpen = false)}>{t('common.close')}</button>
    </div>
</Modal>

<Modal bind:open={noteOpen} title={t('events.att.coach_note')}>
    <form class="grid gap-3" onsubmit={saveNote}>
        {#if noteError}
            <Alert kind="error">{noteError}</Alert>
        {/if}
        <p class="text-sm font-semibold text-ink">{noteRow?.member.name}</p>
        <p class="flex items-center gap-1.5 text-xs font-semibold text-warn-text">
            <Lock size={13} aria-hidden="true"/> {t('events.att.coach_note_private')}</p>
        <label class="form-control w-full">
            <span class="label"><span class="label-text">{t('events.att.coach_note')}</span></span>
            <textarea class="textarea w-full" rows="4" maxlength="500" bind:value={noteText}></textarea>
        </label>
        <div class="modal-action mt-2 flex justify-end gap-2">
            <button class="btn btn-outline" type="button" onclick={() => (noteOpen = false)}>{t('common.cancel')}</button>
            <button class="btn btn-primary" type="submit" disabled={noteBusy}>{t('common.save')}</button>
        </div>
    </form>
</Modal>
