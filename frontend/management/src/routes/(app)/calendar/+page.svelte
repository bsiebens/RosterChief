<script lang="ts">
    import {pageTitle} from '#lib/title.svelte.ts';
    import {goto} from '$app/navigation';
    import {page} from '$app/state';
    import {CalendarDays, ChevronLeft, ChevronRight, Download, Plus, X} from '@lucide/svelte';
    import {canManageSettings} from '#lib/permissions.ts';
    import Alert from '#lib/components/Alert.svelte';
    import EventCard from '#lib/components/events/EventCard.svelte';
    import {usePageHeader} from '#lib/header.svelte.ts';
    import {t} from '#lib/i18n.svelte.ts';
    import {addDays, AGENDA_DAYS, EVENT_KINDS, type EventRow, formatDay, formatLongDay, formatMonth, formatWeekday, isoDate, kindLabelKey, kindStyle, parseIsoDate, sameDay, weekStart} from '#lib/events/types.ts';

    let {data} = $props();

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

    const periodLabel = $derived(data.view === 'month' ? formatMonth(anchor) : `${formatDay(from)} – ${formatDay(to)}`);

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

    const weekdayNames = $derived(Array.from({length: 7}, (_, i) => addDays(weekStart(today), i)));

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
        <ol class="grid gap-5">
            {#each list as d (isoDate(d))}
                <li>
                    <h2 class="mb-2 flex items-baseline gap-2 font-display text-base font-extrabold tracking-[.04em] text-ink uppercase">
                        {formatLongDay(d)}
                        {#if isoDate(d) === todayIso}<span class="badge badge-xs border border-club bg-club text-club-content">{t('events.cal.today')}</span>{/if}
                    </h2>
                    <ul class="grid gap-2 md:grid-cols-2 2xl:grid-cols-3">
                        {#each eventsOn(d) as e (e.id)}
                            <li>
                                <EventCard event={e}/>
                            </li>
                        {/each}
                    </ul>
                </li>
            {/each}
        </ol>
    {/if}
{/snippet}

<svelte:head><title>{pageTitle(t('events.cal.title'))}</title></svelte:head>

<div class="-mx-7 -my-6 flex flex-col">
    <!-- Navigation and view switch. -->
    <div class="flex flex-wrap items-center gap-2 border-b border-line bg-white px-7 py-2.5">
        <div class="flex items-center gap-1">
            <a class="btn btn-outline btn-sm btn-square" href={link({ date: shift.prev })} aria-label={t('events.cal.prev')} title={t('events.cal.prev')} data-sveltekit-noscroll>
                <ChevronLeft size={16}/>
            </a>
            <a class="btn btn-outline btn-sm" href={link({ date: todayIso })} data-sveltekit-noscroll>{t('events.cal.today')}</a>
            <a class="btn btn-outline btn-sm btn-square" href={link({ date: shift.next })} aria-label={t('events.cal.next')} title={t('events.cal.next')} data-sveltekit-noscroll>
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
                <ol class="hidden gap-2 lg:grid lg:grid-cols-7">
                    {#each days as d (isoDate(d))}
                        {@const isToday = sameDay(d, today)}
                        <li class="flex min-w-0 flex-col gap-2 rounded-lg p-1.5 {isToday ? 'bg-white ring-2 ring-club' : ''}" aria-label={formatLongDay(d)}>
                            <h3 class="flex items-baseline gap-1.5 px-1 font-display text-sm font-extrabold tracking-[.04em] text-ink uppercase" aria-current={isToday ? 'date' : undefined}>
                                {formatWeekday(d)} <span class="font-mono text-xs font-normal text-muted">{d.getDate()}</span>
                            </h3>
                            {#each eventsOn(d) as e (e.id)}
                                <EventCard event={e}/>
                            {:else}<p class="px-1 text-xs text-dim">{t('events.cal.nothing')}</p>{/each}
                        </li>
                    {/each}
                </ol>
            {/if}
        {/if}
    </div>
</div>
