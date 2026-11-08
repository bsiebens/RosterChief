<script lang="ts">
    import {pageTitle} from '#lib/title.svelte.ts';
    import {goto} from '$app/navigation';
    import {page} from '$app/state';
    import {CalendarDays, ChevronLeft, ChevronRight, Download, MapPin, Pencil, Plus, Repeat, X} from '@lucide/svelte';
    import {canManageSettings} from '#lib/permissions.ts';
    import Alert from '#lib/components/Alert.svelte';
    import EventCard from '#lib/components/events/EventCard.svelte';
    import {usePageHeader} from '#lib/header.svelte.ts';
    import {t} from '#lib/i18n.svelte.ts';
    import {eventEditHref, eventHref, formatTime, resultStyle, scoreText, type AttendanceCounts} from '#lib/events/types.ts';
    import {addDays, AGENDA_DAYS, EVENT_KINDS, type EventRow, formatDay, formatLongDay, formatMonth, formatWeekday, isoDate, kindLabelKey, kindStyle, parseIsoDate, sameDay, weekStart} from '#lib/events/types.ts';

    import type {PageData} from '../../../routes/(app)/calendar/$types';

    let {data}: {data: PageData} = $props();

    const MAX_IN_CELL = 3;
    const anchor = $derived(parseIsoDate(data.date) ?? new Date());
    const from = $derived(parseIsoDate(data.from) ?? anchor);
    const to = $derived(parseIsoDate(data.to) ?? anchor);
    const today = new Date();
    const todayIso = isoDate(today);

    /** The current URL with some parameters replaced (null / '' removes one). */
    function link(over: Record<string, string | null>): string {
        const q = new URLSearchParams(page.url.search);
        for (const [k, v] of Object.entries(over)) {
            if (v === null || v === '') q.delete(k);
            else q.set(k, v);
        }
        const s = q.toString();
        return s ? `?${s}` : '?';
    }

    const go = (over: Record<string, string | null>) => goto(link(over), {reset: false});

    const shift = $derived.by(() => {
        const a = anchor;
        if (data.view === 'month') return {prev: isoDate(new Date(a.getFullYear(), a.getMonth() - 1, 1)), next: isoDate(new Date(a.getFullYear(), a.getMonth() + 1, 1))};
        const step = data.view === 'week' ? 7 : AGENDA_DAYS;
        return {prev: isoDate(addDays(a, -step)), next: isoDate(addDays(a, step))};
    });

    const periodLabel = $derived(data.view === 'month' ? formatMonth(anchor) : data.view === 'agenda' && data.seasonName ? data.seasonName : `${formatDay(from)} – ${formatDay(to)}`);

    /** Every day of the loaded range. */
    const days = $derived.by(() => {
        const out: Date[] = [];
        for (let d = from; d <= to; d = addDays(d, 1)) out.push(d);
        return out;
    });

    /** Events per local day (an event spanning several days shows on each of them). */
    const byDay = $derived.by(() => {
        const map = new Map<string, EventRow[]>();
        for (const e of data.events) {
            const s = new Date(e.start);
            const startDay = new Date(s.getFullYear(), s.getMonth(), s.getDate());
            const end = e.end ? new Date(e.end) : s;
            // An event ending at midnight does not spill into the next day.
            const endDay = new Date(end.getFullYear(), end.getMonth(), end.getDate());
            const last = endDay > startDay && end.getHours() === 0 && end.getMinutes() === 0 ? addDays(endDay, -1) : endDay;
            for (let d = startDay < from ? from : startDay; d <= last && d <= to; d = addDays(d, 1)) {
                const k = isoDate(d);
                map.set(k, [...(map.get(k) ?? []), e]);
            }
        }
        return map;
    });
    const eventsOn = (d: Date) => byDay.get(isoDate(d)) ?? [];
    const agendaDays = $derived(days.filter((d) => eventsOn(d).length));

    // Agenda: scroll inside the box to today, or to the first day with events after it.
    let agendaBox = $state<HTMLElement | null>(null);
    const focusDay = $derived(agendaDays.find((d) => isoDate(d) >= todayIso) ?? agendaDays[agendaDays.length - 1]);
    function scrollToFocus() {
        if (!agendaBox || data.view !== 'agenda' || !focusDay) return;
        const row = agendaBox.querySelector<HTMLElement>(`[data-day="${isoDate(focusDay)}"]`);
        const head = agendaBox.querySelector('thead')?.getBoundingClientRect().height ?? 0;
        if (row) agendaBox.scrollTop = row.getBoundingClientRect().top - agendaBox.getBoundingClientRect().top + agendaBox.scrollTop - head;
    }
    $effect(() => {
        data.events;
        agendaBox;
        scrollToFocus();
    });

    const weekdayNames = $derived(Array.from({length: 7}, (_, i) => addDays(weekStart(today), i)));


    // -- week grid: one column per day, one row per hour; events are placed by their start and length --
    const HOUR_PX = 56;
    const MIN_BLOCK_PX = 26;
    type Seg = {e: EventRow; top: number; height: number; lane: number; lanes: number};
    const minutesOfDay = (d: Date) => d.getHours() * 60 + d.getMinutes();
    /** The part of each event that falls on day `d`, in minutes since midnight. */
    function rawSegments(d: Date) {
        const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
        const dayEnd = dayStart + 86_400_000;
        return eventsOn(d).map((e) => {
            const s = new Date(e.start).getTime();
            const en = e.end ? new Date(e.end).getTime() : s + 3_600_000;
            const a = Math.max(s, dayStart);
            const b = Math.min(Math.max(en, s + 30 * 60_000), dayEnd);
            return {e, from: Math.round((a - dayStart) / 60000), to: Math.max(Math.round((b - dayStart) / 60000), Math.round((a - dayStart) / 60000) + 30)};
        });
    }
    // Always 06:00 until midnight.
    const gridRange = {start: 6, end: 24};
    const hours = $derived(Array.from({length: gridRange.end - gridRange.start}, (_, i) => gridRange.start + i));
    /** Side-by-side lanes for events that overlap in time. */
    function layout(d: Date): Seg[] {
        const items = rawSegments(d).sort((x, y) => x.from - y.from || y.to - x.to);
        const out: (Seg & {from: number; to: number})[] = [];
        let cluster: typeof out = [];
        let clusterEnd = -1;
        const close = () => {
            const lanes = Math.max(1, ...cluster.map((c) => c.lane + 1));
            for (const c of cluster) c.lanes = lanes;
            cluster = [];
        };
        for (const it of items) {
            if (it.from >= clusterEnd && cluster.length) close();
            const used = new Set(cluster.filter((c) => c.to > it.from).map((c) => c.lane));
            let lane = 0;
            while (used.has(lane)) lane++;
            const seg = {e: it.e, from: it.from, to: it.to, lane, lanes: 1, top: (it.from - gridRange.start * 60) / 60 * HOUR_PX, height: Math.max((it.to - it.from) / 60 * HOUR_PX, MIN_BLOCK_PX)};
            cluster.push(seg);
            out.push(seg);
            clusterEnd = Math.max(clusterEnd, it.to);
        }
        if (cluster.length) close();
        return out;
    }
    const nowTop = $derived((minutesOfDay(today) - gridRange.start * 60) / 60 * HOUR_PX);
    const hourLabel = (h: number) => `${String(h).padStart(2, '0')}:00`;

    function attSummary(c: AttendanceCounts): string {
        if (!c.total) return '';
        const parts = [t('events.att.sum_in', {n: c.present + c.selected}), t('events.att.sum_out', {n: c.absent + c.excused + c.not_selected})];
        if (c.maybe) parts.push(t('events.att.sum_maybe', {n: c.maybe}));
        parts.push(t('events.att.sum_none', {n: c.no_response}));
        return parts.join(' · ');
    }
    const audienceOf = (e: EventRow) => (e.club_wide ? t('events.whole_club') : [...e.teams, ...e.groups].map((x) => x.name).join(', '));

    const filtered = $derived(!!(data.filters.team || data.filters.location || data.filters.opponent || data.filters.kind || data.filters.mine));
    const views = $derived([
        {key: 'month', label: t('events.cal.view.month')},
        {key: 'week', label: t('events.cal.view.week')},
        {key: 'agenda', label: t('events.cal.view.agenda')}
    ]);

    // Setting up fixture imports is for club admins (the settings permission); everyone else only sees the sources on a team's page.
    const canImport = $derived(canManageSettings(data.me));

    usePageHeader(() => ({
        title: t('events.cal.title'),
        context: data.events.length === 1 ? t('events.cal.count_one') : t('events.cal.count', {n: data.events.length}),
        actions: data.options?.can_create || canImport ? actions : null
    }));
</script>

{#snippet actions()}
    {#if canImport}
        <a class="btn btn-outline gap-2" href="/calendar/import">
            <Download size={16}/> {t('calimport.button')}</a>
    {/if}
    {#if data.options?.can_create}
        <a class="btn btn-primary gap-2" href="/calendar/events/new">
            <Plus size={16}/> {t('events.add')}</a>
    {/if}
{/snippet}

{#snippet agenda(list: Date[])}
    {#if !list.length}
        <div class="card card-body items-center py-10 text-center text-muted">
            <CalendarDays size={28} aria-hidden="true"/>
            <p>{filtered ? t('events.cal.empty_filtered') : t('events.cal.empty')}</p>
            {#if data.filters.mine}<p class="text-xs">{t('events.cal.mine_help')}</p>{/if}
        </div>
    {:else}
        <div bind:this={agendaBox} class="card overflow-auto" style="max-height: calc(100vh - 15rem)">
            <table class="table">
                <thead class="sticky top-0 z-10 bg-subhead">
                <tr>
                    <th class="w-32">{t('events.cal.col.time')}</th>
                    <th>{t('events.cal.col.event')}</th>
                    <th class="hidden md:table-cell">{t('events.cal.col.audience')}</th>
                    <th class="hidden lg:table-cell">{t('events.cal.col.location')}</th>
                    <th class="hidden xl:table-cell">{t('events.cal.col.attendance')}</th>
                    <th class="w-10"><span class="sr-only">{t('common.edit')}</span></th>
                </tr>
                </thead>
                <tbody>
                {#each list as d (isoDate(d))}
                    <tr class="bg-paper" data-day={isoDate(d)}>
                        <th colspan="6" class="px-4 py-2 text-left font-display text-sm font-extrabold tracking-[.04em] text-ink uppercase">
                            {formatLongDay(d)}
                            {#if isoDate(d) === todayIso}<span class="badge badge-xs ml-2 border border-club bg-club text-club-content">{t('events.cal.today')}</span>{/if}
                        </th>
                    </tr>
                    {#each eventsOn(d) as e (e.id)}
                        {@const style = kindStyle(e.kind)}
                        <tr class="hover:bg-surface {e.cancelled ? 'opacity-60' : ''}">
                            <td class="font-mono text-xs whitespace-nowrap text-muted">{formatTime(e.start)}{#if e.end} – {formatTime(e.end)}{/if}</td>
                            <td>
                                <a class="flex items-center gap-2 font-semibold text-ink hover:underline {e.cancelled ? 'line-through' : ''}" href={eventHref(e.id)}>
                                    <span class="h-2.5 w-2.5 shrink-0 rounded-full {style.dot}" aria-hidden="true"></span>{e.title}
                                    {#if e.series_id}<Repeat size={12} class="text-dim" aria-label={t('events.series')}/>{/if}
                                    {#if scoreText(e)}<span class="rounded border px-1.5 font-mono text-xs font-bold {resultStyle(e)}">{scoreText(e)}</span>{/if}
                                </a>
                                <span class="mt-0.5 flex flex-wrap items-center gap-1.5">
                                    <span class="badge badge-xs border {style.chip}">{t(kindLabelKey(e.kind))}</span>
                                    {#if e.cancelled}<span class="badge badge-xs badge-error">{t('events.cancelled')}</span>{/if}
                                    {#if e.opponent}<span class="text-xs text-muted">{t('events.vs', {name: e.opponent.name})}</span>{/if}
                                </span>
                            </td>
                            <td class="hidden text-sm md:table-cell">{audienceOf(e) || '—'}</td>
                            <td class="hidden text-sm lg:table-cell">
                                {#if e.location}<span class="flex items-center gap-1"><MapPin size={12} class="shrink-0 text-dim" aria-hidden="true"/>{e.location.name}{e.location.city ? `, ${e.location.city}` : ''}</span>{:else}—{/if}
                            </td>
                            <td class="hidden font-mono text-xs whitespace-nowrap text-muted xl:table-cell">{attSummary(e.attendance) || '—'}</td>
                            <td>{#if e.can_edit}<a class="btn btn-ghost btn-xs btn-square" href={eventEditHref(e.id)} aria-label={t('events.edit_named', {name: e.title})} title={t('common.edit')}><Pencil size={13}/></a>{/if}</td>
                        </tr>
                    {/each}
                {/each}
                </tbody>
            </table>
        </div>
    {/if}
{/snippet}

<svelte:head><title>{pageTitle(t('events.cal.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
    <!-- Navigation and view switch. -->
    <div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5">
        <div class="flex items-center gap-1">
            <a class="btn btn-outline btn-sm btn-square {data.view === 'agenda' ? 'hidden' : ''}" href={link({ date: shift.prev })} aria-label={t('events.cal.prev')} title={t('events.cal.prev')} data-sveltekit-noscroll>
                <ChevronLeft size={16}/>
            </a>
            <a class="btn btn-outline btn-sm" href={link({ date: todayIso })} data-sveltekit-noscroll onclick={() => data.view === 'agenda' && scrollToFocus()}>{t('events.cal.today')}</a>
            <a class="btn btn-outline btn-sm btn-square {data.view === 'agenda' ? 'hidden' : ''}" href={link({ date: shift.next })} aria-label={t('events.cal.next')} title={t('events.cal.next')} data-sveltekit-noscroll>
                <ChevronRight size={16}/>
            </a>
        </div>
        <h2 class="font-display text-lg font-extrabold tracking-[.04em] text-ink uppercase" aria-live="polite">{periodLabel}</h2>
        <div class="flex-1"></div>
        <div class="hidden w-fit items-center gap-1 rounded-full bg-primary p-1 lg:flex" role="group" aria-label={t('events.cal.view')}>
            {#each views as v (v.key)}
                <a
                        href={link({ view: v.key === 'month' ? null : v.key })}
                        data-sveltekit-noscroll
                        data-sveltekit-keepfocus
                        aria-current={data.view === v.key ? 'true' : undefined}
                        class="rounded-full px-3 py-1.5 font-display text-xs font-bold tracking-[.08em] uppercase {data.view === v.key ? 'bg-primary-content text-primary' : 'text-primary-dim hover:text-primary-content'}">{v.label}</a>
            {/each}
        </div>
    </div>

    <!-- Filters. -->
    <div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5" role="search" aria-label={t('events.cal.filters')}>
        <select class="select select-sm w-auto" aria-label={t('events.cal.f.team')} value={data.filters.team} onchange={(e) => go({ team: e.currentTarget.value })}>
            <option value="">{t('events.cal.f.all_teams')}</option>
            {#each data.teams as team (team.id)}
                <option value={team.id}>{team.name}</option>
            {/each}
        </select>
        <select class="select select-sm w-auto" aria-label={t('events.cal.f.location')} value={data.filters.location} onchange={(e) => go({ location: e.currentTarget.value })}>
            <option value="">{t('events.cal.f.all_locations')}</option>
            {#each data.options?.locations ?? [] as l (l.id)}
                <option value={l.id}>{l.name}{l.city ? ` (${l.city})` : ''}</option>
            {/each}
        </select>
        <select class="select select-sm w-auto" aria-label={t('events.cal.f.opponent')} value={data.filters.opponent} onchange={(e) => go({ opponent: e.currentTarget.value })}>
            <option value="">{t('events.cal.f.all_opponents')}</option>
            {#each data.options?.opponents ?? [] as o (o.id)}
                <option value={o.id}>{o.name}</option>
            {/each}
        </select>
        <select class="select select-sm w-auto" aria-label={t('events.cal.f.kind')} value={data.filters.kind} onchange={(e) => go({ kind: e.currentTarget.value })}>
            <option value="">{t('events.cal.f.all_kinds')}</option>
            {#each EVENT_KINDS as k (k)}
                <option value={k}>{t(kindLabelKey(k))}</option>
            {/each}
        </select>
        <label class="flex cursor-pointer items-center gap-1.5 text-sm text-slate">
            <input type="checkbox" class="checkbox" checked={data.filters.mine} onchange={(e) => go({ mine: e.currentTarget.checked ? '1' : null })}/>
            {t('events.cal.f.mine')}
        </label>
        {#if filtered}<a class="btn btn-outline btn-sm gap-2" href={link({ team: null, location: null, opponent: null, kind: null, mine: null })} data-sveltekit-noscroll>
            <X size={14}/> {t('members.list.clear')}</a>{/if}
        <div class="flex-1"></div>
        <ul class="hidden flex-wrap items-center gap-x-3 gap-y-1 xl:flex" aria-label={t('events.cal.legend')}>
            {#each EVENT_KINDS as k (k)}
                <li class="flex items-center gap-1.5 text-xs text-muted"><span class="h-2 w-2 rounded-full {kindStyle(k).dot}" aria-hidden="true"></span>{t(kindLabelKey(k))}</li>
            {/each}
        </ul>
    </div>

    <div class="px-7 py-5">
        {#if data.error}
            <Alert kind="error">{data.error.message || t('common.error.generic')}</Alert>
        {:else}
            <!-- Narrow screens: always the agenda of the loaded range. -->
            <div class={data.view === 'agenda' ? '' : 'lg:hidden'}>{@render agenda(agendaDays)}</div>

            {#if data.view === 'month'}
                <div class="card hidden overflow-hidden lg:block">
                    <div class="grid grid-cols-7 border-b border-line bg-subhead" aria-hidden="true">
                        {#each weekdayNames as w (w.getDay())}
                            <div class="px-2 py-1.5 font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{formatWeekday(w)}</div>
                        {/each}
                    </div>
                    <ol class="grid grid-cols-7">
                        {#each days as d, i (isoDate(d))}
                            {@const list = eventsOn(d)}
                            {@const inMonth = d.getMonth() === anchor.getMonth()}
                            {@const isToday = sameDay(d, today)}
                            <li class="flex min-h-28 min-w-0 flex-col gap-0.5 border-line p-1 {i % 7 ? 'border-l' : ''} {i >= 7 ? 'border-t' : ''} {inMonth ? 'bg-white' : 'bg-paper'}" aria-label={formatLongDay(d)}>
                                <a
                                        href={link({ view: 'agenda', date: isoDate(d) })}
                                        class="mb-0.5 inline-flex h-6 min-w-6 items-center justify-center self-start rounded-full px-1 font-mono text-xs {isToday ? 'bg-club font-bold text-club-content' : inMonth ? 'text-ink hover:bg-paper' : 'text-dim hover:bg-white'}"
                                        aria-label={t('events.cal.day_agenda', { day: formatLongDay(d) })}
                                        aria-current={isToday ? 'date' : undefined}>{d.getDate()}</a>
                                {#each list.slice(0, MAX_IN_CELL) as e (e.id)}
                                    <EventCard event={e} variant="chip"/>
                                {/each}
                                {#if list.length > MAX_IN_CELL}
                                    <a class="link px-1.5 text-xs font-semibold text-muted" href={link({ view: 'agenda', date: isoDate(d) })}>{t('events.cal.more', {n: list.length - MAX_IN_CELL})}</a>
                                {/if}
                            </li>
                        {/each}
                    </ol>
                </div>
            {:else if data.view === 'week'}
                <div class="card hidden overflow-hidden lg:block">
                    <div class="grid grid-cols-[3.5rem_repeat(7,minmax(0,1fr))] border-b border-line bg-subhead">
                        <div></div>
                        {#each days as d (isoDate(d))}
                            {@const isToday = sameDay(d, today)}
                            <div class="border-l border-line px-2 py-2 text-center" aria-current={isToday ? 'date' : undefined}>
                                <span class="font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{formatWeekday(d)}</span>
                                <span class="ml-1 inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1 font-mono text-xs {isToday ? 'bg-club font-bold text-club-content' : 'text-ink'}">{d.getDate()}</span>
                            </div>
                        {/each}
                    </div>
                    <div class="grid grid-cols-[3.5rem_repeat(7,minmax(0,1fr))]">
                        <div class="relative" style="height: {hours.length * HOUR_PX}px" aria-hidden="true">
                            {#each hours as h, i (h)}
                                <span class="absolute right-2 -translate-y-1/2 font-mono text-[11px] text-dim" style="top: {i * HOUR_PX}px; {i === 0 ? 'transform: none' : ''}">{hourLabel(h)}</span>
                            {/each}
                        </div>
                        {#each days as d (isoDate(d))}
                            {@const isToday = sameDay(d, today)}
                            <div class="relative border-l border-line {isToday ? 'bg-club/[0.03]' : ''}" style="height: {hours.length * HOUR_PX}px; background-image: repeating-linear-gradient(to bottom, transparent 0, transparent {HOUR_PX - 1}px, var(--color-line, #e5e7eb) {HOUR_PX - 1}px, var(--color-line, #e5e7eb) {HOUR_PX}px)" aria-label={formatLongDay(d)}>
                                {#each layout(d) as sg (sg.e.id)}
                                    {@const style = kindStyle(sg.e.kind)}
                                    <a href={eventHref(sg.e.id)}
                                       class="absolute overflow-hidden rounded border border-l-4 border-line bg-white px-1.5 py-1 text-xs shadow-sm hover:z-10 hover:shadow-md focus:z-10 {style.bar} {sg.e.cancelled ? 'bg-subhead opacity-70' : ''}"
                                       style="top: {sg.top}px; height: {sg.height - 2}px; left: calc({(sg.lane / sg.lanes) * 100}% + 2px); width: calc({100 / sg.lanes}% - 4px)"
                                       title="{formatTime(sg.e.start)} {sg.e.title}">
                                        <span class="block font-mono text-[10px] text-muted">{formatTime(sg.e.start)}{#if sg.e.end} – {formatTime(sg.e.end)}{/if}{#if scoreText(sg.e)} <span class="ml-1 rounded border px-1 font-bold {resultStyle(sg.e)}">{scoreText(sg.e)}</span>{/if}</span>
                                        <span class="block truncate font-semibold text-ink {sg.e.cancelled ? 'line-through' : ''}">{sg.e.title}</span>
                                        {#if sg.height > 64 && sg.e.location}<span class="block truncate text-[11px] text-muted">{sg.e.location.name}</span>{/if}
                                    </a>
                                {/each}
                                {#if isToday && nowTop >= 0 && nowTop <= hours.length * HOUR_PX}
                                    <div class="pointer-events-none absolute inset-x-0 z-20 border-t-2 border-club" style="top: {nowTop}px"><span class="absolute -top-1.5 -left-1 h-2.5 w-2.5 rounded-full bg-club"></span></div>
                                {/if}
                            </div>
                        {/each}
                    </div>
                </div>
            {/if}
        {/if}
    </div>
</div>
