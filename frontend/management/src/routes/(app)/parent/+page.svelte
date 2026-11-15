<script lang="ts">
	import { ArrowLeft, Bell, CalendarDays, ListChecks, MapPin, Users, ChevronRight, CircleCheck, ClipboardList, FileText, Handshake, KeyRound, List, UserRound, UsersRound, Wallet, Whistle } from '@lucide/svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { formatDateTime, formatLongDay, formatTime, kindLabelKey, kindStyle, statusLabelKey, statusStyle } from '#lib/events/types.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { EventExtras, OfficiatingOpportunity, OpenTask, PortalEvent, PortalEventDisplay, PortalTask } from '#lib/portal.ts';
	import { day, money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import AccountMenu from '#lib/components/AccountMenu.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import InstallPush from '#lib/components/InstallPush.svelte';
	import PersonFilter from '#lib/components/PersonFilter.svelte';
	import PortalEventDate from '#lib/components/PortalEventDate.svelte';
	import ReplyRow from '#lib/components/portal/ReplyRow.svelte';
	import EventHero from '#lib/components/portal/EventHero.svelte';
	import MonthPicker from '#lib/components/portal/MonthPicker.svelte';
	import News from '#lib/components/portal/News.svelte';
	import SampleBadge from '#lib/components/SampleBadge.svelte';
	import OfficiatingRow from '#lib/components/portal/OfficiatingRow.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';

	let { data } = $props();
	let selected = $state('');
	let busy = $state('');
	let errors = $state<Record<string, string>>({});
	let month = $state(new Date());
	let pickedDay = $state('');
	let monthOpen = $state(false);
	const dateKey = (date: Date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
	const tab = $derived(page.url.searchParams.get('tab') || page.url.hash.slice(1) || 'home');
	const crewOnly = $derived(tab === 'calendar' && page.url.searchParams.get('crew') === '1');
	const VIEWS = ['month', 'week', 'list'] as const;
	const view = $derived(VIEWS.find((v) => v === page.url.searchParams.get('view')) ?? 'month');
	const mondayOf = (date: Date) => {
		const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
		day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
		return day;
	};
	let weekStart = $state(mondayOf(new Date()));
	const weekDays = $derived(Array.from({ length: 7 }, (_, i) => new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + i)));
	const moveWeek = (step: number) => (weekStart = new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + 7 * step));
	const labels: Record<string, string> = { home: 'portal.home', calendar: 'shell.nav.calendar', news: 'shell.nav.news', me: 'portal.me', forms: 'portal.forms', payments: 'portal.payments' };
	// `data.home` is briefly gone while the app navigates away (to the manage app, say): the header code below still runs then, so these reads must not throw.
	const people = $derived(data.home?.people ?? []);
	const viewer = $derived(people.find((person) => person.id === data.home?.viewer));
	const hour = new Date().getHours();
	const greeting = $derived(t(hour < 12 ? 'portal.greeting_morning' : hour < 18 ? 'portal.greeting_afternoon' : 'portal.greeting_evening', { name: viewer?.first_name || viewer?.name || '' }));
	const title = $derived(tab === 'home' ? greeting : t(labels[tab] ?? labels.home));
	const events = $derived(data.home.events.filter((event) => !selected || event.member_id === selected));
	const memberName = (id: string) => people.find((person) => person.id === id)?.name ?? '';
	const crest = $derived(data.branding?.logo_url);
	const background = $derived(data.home.event_background);
	const canReply = (event: PortalEvent) => !event.cancelled && event.blocked !== 'not_invited' && Date.parse(event.start) > Date.now() && (event.status === 'selected' || !event.deadline || Date.parse(event.deadline) >= Date.now());
	const going = (event: PortalEvent) => event.status === 'present' || event.status === 'selected';
	const nextEvents = $derived.by(() => {
		const next = new Map<string, PortalEvent[]>();
		const included = new Set<string>();
		for (const event of events) {
			if (included.has(event.member_id) || Date.parse(event.start) <= Date.now() || event.cancelled) continue;
			if (!going(event) && !['no_response', 'maybe'].includes(event.status)) continue;
			included.add(event.member_id);
			const rows = next.get(event.event_id) ?? [];
			rows.push(event);
			next.set(event.event_id, rows);
		}
		return [...next.values()];
	});
	const needsReply = $derived(events.filter((event) => ['no_response', 'maybe'].includes(event.status) && canReply(event) && !nextEvents.some((rows) => rows.some((row) => row.id === event.id))));
	const upcomingTwoWeeks = $derived.by(() => {
		const now = new Date();
		const end = new Date(now);
		end.setDate(end.getDate() + 14);
		const seen = new Set(nextEvents.map(([event]) => event.event_id));
		return events.filter((event) => {
			const start = new Date(event.start);
			if (event.cancelled || start <= now || start >= end || seen.has(event.event_id)) return false;
			seen.add(event.event_id);
			return true;
		});
	});

	const monthKey = $derived(dateKey(month).slice(0, 7));
	const offers = $derived((data.home.officiating ?? []).filter((offer) => !selected || offer.member_id === selected));
	const officiatingInvites = $derived(offers.filter((offer) => offer.status === 'invited' && offer.signup_open));
	type CalendarEntry = { event: PortalEventDisplay; attendance: PortalEvent[]; officiating: OfficiatingOpportunity[] };
	function calendarEntries(rows: PortalEvent[], opportunities: OfficiatingOpportunity[]) {
		const grouped = new Map<string, CalendarEntry>();
		const groupFor = (event: PortalEventDisplay) => {
			let group = grouped.get(event.event_id);
			if (!group) {
				group = { event, attendance: [], officiating: [] };
				grouped.set(event.event_id, group);
			}
			return group;
		};
		for (const row of rows) groupFor(row).attendance.push(row);
		for (const offer of opportunities) groupFor(offer).officiating.push(offer);
		return [...grouped.values()].sort((a, b) => Date.parse(a.event.start) - Date.parse(b.event.start));
	}
	const calendar = $derived(calendarEntries(events, offers));
	const eventDays = $derived.by(() => {
		const days = new Map<string, string[]>();
		for (const { event } of calendar) {
			const key = dateKey(new Date(event.start));
			const kinds = days.get(key) ?? [];
			if (!kinds.includes(event.kind)) kinds.push(event.kind);
			days.set(key, kinds);
		}
		return days;
	});
	const kindsPresent = $derived([...new Set(calendar.map(({ event }) => event.kind))]);
	const entriesOn = (day: Date) => calendar.filter(({ event }) => dateKey(new Date(event.start)) === dateKey(day));
	const listGroups = $derived.by(() => {
		const groups = new Map<string, { key: string; date: Date; entries: CalendarEntry[] }>();
		for (const entry of calendar) {
			const date = new Date(entry.event.start);
			const key = dateKey(date);
			groups.set(key, groups.get(key) ?? { key, date, entries: [] });
			groups.get(key)?.entries.push(entry);
		}
		return [...groups.values()].sort((a, b) => a.key.localeCompare(b.key));
	});
	const todayKey = $derived(listGroups.find((group) => group.key >= dateKey(new Date()))?.key ?? '');
	/** Opens the list at today: the past stays above, scrolled out of sight. */
	function scrollToToday(node: HTMLElement) {
		const target = node.querySelector<HTMLElement>('[data-today]');
		if (target) node.scrollTop = target.offsetTop - node.offsetTop;
	}
	const agenda = $derived(calendar.filter(({ event, officiating }) => {
		if (crewOnly) return officiating.length > 0;
		const key = dateKey(new Date(event.start));
		return pickedDay ? key === pickedDay : key.startsWith(monthKey);
	}));
	const detail = $derived.by(() => {
		const id = page.url.searchParams.get('event');
		if (!id) return undefined;
		return calendarEntries(
			data.home.events.filter((event) => event.event_id === id),
			(data.home.officiating ?? []).filter((offer) => offer.event_id === id)
		)[0];
	});
	const offerKey = (offer: OfficiatingOpportunity) => `${offer.event_id}-${offer.role}-${offer.member_id}`;

	const profiles = $derived(new Map((data.profile ?? []).map((person) => [person.id, person])));
	const teamsOf = (id: string) => (profiles.get(id)?.teams ?? []).map((team) => `${team.name}${team.jersey_number !== null ? ` · #${team.jersey_number}` : ''}`).join(', ');
	const firstName = (id: string) => people.find((person) => person.id === id)?.first_name || memberName(id);
	const filteredFees = $derived(data.home.fees.filter((fee) => !selected || fee.member_id === selected));
	const openFees = $derived(filteredFees.filter((fee) => Number(fee.balance) > 0));
	const filteredForms = $derived(data.home.forms.filter((form) => !selected || form.member_id === selected));
	const calendarLabel = $derived(people.length > 1 ? t('portal.calendar') : t('shell.nav.calendar'));
	const openTasks = $derived<OpenTask[]>(data.home?.tasks ?? []);
	const hasAttention = $derived(!!(needsReply.length || filteredForms.length || openFees.length || officiatingInvites.length || openTasks.length));
	const attentionCount = $derived(needsReply.length + filteredForms.length + openFees.length + officiatingInvites.length + openTasks.length);
	const viewLink = (v: string) => `/parent?tab=calendar${v === 'month' ? '' : `&view=${v}`}`;
	const calendarLink = $derived(crewOnly ? '/parent?tab=calendar&crew=1' : viewLink(view));
	const eventLink = (id: string) => tab === 'calendar' ? `${calendarLink}&event=${id}` : `/parent?event=${id}`;

	// The event drawer's line-up, tasks and attachments: read when it opens.
	let extras = $state<EventExtras | null>(null);
	const detailId = $derived(detail?.event.event_id ?? '');
	async function loadExtras(id: string) {
		const result = await api<EventExtras>('GET', `/portal/events/${id}`);
		if (result.ok && result.data && detailId === id) extras = result.data;
	}
	$effect(() => {
		const id = detailId;
		extras = null;
		if (id) void loadExtras(id);
	});
	async function claim(eventId: string, taskId: string, take: boolean) {
		if (busy) return;
		busy = taskId;
		errors[taskId] = '';
		try {
			const result = await api<PortalTask>(take ? 'POST' : 'DELETE', `/portal/events/${eventId}/tasks/${taskId}/claim`);
			if (result.ok) await Promise.all([loadExtras(eventId), invalidateAll()]);
			else errors[taskId] = result.message ?? t('common.error.generic');
		} finally {
			busy = '';
		}
	}
	const sizeText = (bytes: number) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

	usePageHeader(() => ({ title, context: tab === 'home' ? formatLongDay(new Date()) : undefined, actions: tab === 'home' && people.length > 1 ? headerPeople : tab === 'calendar' ? calendarActions : undefined }));

	async function reply(event: PortalEvent, status: string, note: string): Promise<boolean> {
		if (busy) return false;
		busy = event.id;
		errors[event.id] = '';
		try {
			const result = await api<PortalEvent>('POST', `/portal/attendance/${event.id}`, { body: { status, note } });
			if (result.ok) {
				await invalidateAll();
				return true;
			}
			errors[event.id] = result.code === 'late_installment' ? t('portal.late') : (result.fields.note ?? result.message ?? t('common.error.generic'));
			return false;
		} finally {
			busy = '';
		}
	}
	async function officiate(offer: OfficiatingOpportunity, available: boolean) {
		if (busy) return;
		const key = offerKey(offer);
		busy = key;
		errors[key] = '';
		try {
			const result = await api('POST', `/portal/officiating/${offer.event_id}/${offer.role}`, { body: { member_id: offer.member_id, available } });
			if (result.ok) await invalidateAll();
			else errors[key] = result.message ?? t('common.error.generic');
		} finally {
			busy = '';
		}
	}

</script>

<svelte:head><title>{pageTitle(title)}</title></svelte:head>

{#snippet peoplePicker()}
	<PersonFilter {people} bind:selected viewer={data.home.viewer} />
{/snippet}

{#snippet rsvp(event: PortalEvent, quick = false, dark = false)}
	<ReplyRow {event} name={memberName(event.member_id)} photo={people.find((person) => person.id === event.member_id)?.photo_url} canReply={canReply(event)} {quick} {dark} busy={busy === event.id} error={errors[event.id] ?? ''} onreply={(status, note) => reply(event, status, note)} />
{/snippet}

{#snippet officiating(offer: OfficiatingOpportunity)}
	<OfficiatingRow {offer} name={memberName(offer.member_id)} busy={!!busy} error={errors[offerKey(offer)] ?? ''} onreply={(available) => officiate(offer, available)} />
{/snippet}

{#snippet formsList()}
	<section>
		<div class="card divide-y divide-rule overflow-hidden">
			{#each filteredForms as form (`${form.id}-${form.member_id}`)}
				<a class="flex min-h-14 items-center justify-between gap-3 px-4 py-3 hover:bg-subhead" href="/parent/forms/{form.id}/{form.member_id}">
					<span><span class="block font-semibold text-ink">{form.title}</span><span class="block text-xs text-muted">{memberName(form.member_id)}{form.closes_at ? ` · ${t('pubform.closes', { date: formatDateTime(form.closes_at) })}` : ''}</span></span>
					<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
				</a>
			{:else}
				<p class="p-4 text-sm text-muted">{t('portal.no_forms')}</p>
			{/each}
		</div>
	</section>
{/snippet}

{#snippet payments()}
	<section class="grid gap-2">
		<div class="card divide-y divide-rule overflow-hidden">
			{#each filteredFees as fee (`${fee.member_id}-${fee.season}`)}
				<div class="grid gap-1 px-4 py-3">
					<p class="font-semibold text-ink">{memberName(fee.member_id)} · {fee.season}</p>
					<p class="text-sm text-muted">{t('portal.fee', { paid: money(fee.paid), balance: money(fee.balance) })}</p>
					{#if fee.next_installment}
						<p class="text-sm {fee.next_installment.late ? 'font-semibold text-danger' : 'text-muted'}">{t(fee.next_installment.late ? 'portal.installment_late' : 'portal.installment_next', { date: day(fee.next_installment.due_date), amount: money(fee.next_installment.remaining) })}</p>
					{/if}
				</div>
			{:else}
				{#if !data.home.invoices.length}<p class="p-4 text-sm text-muted">{t('portal.no_payments')}</p>{/if}
			{/each}
			{#each data.home.invoices as invoice (invoice.id)}
				<a class="flex min-h-12 items-center gap-2 px-4 py-3 font-semibold hover:bg-subhead" href="/api/v1/portal/invoices/{invoice.id}.pdf" target="_blank" rel="noreferrer"><FileText size={16} aria-hidden="true" />{t('portal.invoice', { number: invoice.number })}<ChevronRight size={16} class="ml-auto text-muted" aria-hidden="true" /></a>
			{/each}
		</div>
	</section>
{/snippet}

{#snippet viewSwitch()}
	<div class="flex gap-1" role="group" aria-label={t('portal.calendar_view')}>
		{#each VIEWS as v (v)}
			<a class="btn {view === v ? '' : 'btn-outline'}" href={viewLink(v)} aria-current={view === v ? 'page' : undefined}>{t(`portal.view_${v}`)}</a>
		{/each}
	</div>
{/snippet}

{#snippet calendarActions()}
	<div class="hidden items-center gap-2 md:flex">
		{@render viewSwitch()}
		<button class="btn btn-outline gap-1.5" type="button" disabled title={t('portal.sample_help')}><CalendarDays size={14} aria-hidden="true" />{t('portal.sync_calendar')}</button>
		<SampleBadge what="calendar-sync" />
	</div>
{/snippet}

{#snippet eventRow(entry: CalendarEntry, tile = true)}
	{@const event = entry.event}
	<div class="grid gap-x-4 gap-y-1 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,25rem)]">
		<div class="flex min-w-0 gap-3">
			{#if tile}<PortalEventDate start={event.start} />{:else}<span class="w-12 shrink-0 pt-2.5 font-mono text-xs text-muted">{formatTime(event.start)}</span>{/if}
			<a class="flex min-h-10 min-w-0 flex-1 items-start gap-2 hover:text-brand" href={eventLink(event.event_id)} data-sveltekit-reset="false">
				<span class="mt-2 size-2 shrink-0 rounded-full {kindStyle(event.kind).dot}"></span>
				<span class="min-w-0 flex-1">
					<span class="block font-semibold text-ink">{event.title}</span>
					<span class="block truncate text-xs text-muted">{tile ? formatTime(event.start) : ''}{tile && event.location ? ' · ' : ''}{event.location}</span>
				</span>
				{#if event.score_for != null && event.score_against != null}<span class="font-mono text-sm font-bold">{event.score_for}–{event.score_against}</span>{:else}<ChevronRight size={16} class="mt-1 shrink-0 text-muted md:hidden" aria-hidden="true" />{/if}
			</a>
		</div>
		<div class="min-w-0 divide-y divide-rule pl-[3.75rem] md:pl-0">
			{#each entry.attendance as row (row.id)}{@render rsvp(row, true)}{/each}
			{#each entry.officiating as offer (offerKey(offer))}{@render officiating(offer)}{/each}
		</div>
	</div>
{/snippet}

{#snippet headerPeople()}
	<div class="hidden xl:block"><PersonFilter {people} bind:selected viewer={data.home?.viewer} compact /></div>
{/snippet}

{#snippet week()}
	{#if upcomingTwoWeeks.length}
		<section class="card card-body" aria-label={t('portal.next_fourteen_days')}>
			<h2 class="card-title"><CalendarDays size={16} aria-hidden="true" />{t('portal.next_fourteen_days')}<a class="link ml-auto text-sm font-semibold" href="/parent?tab=calendar">{calendarLabel} →</a></h2>
			<div class="divide-y divide-rule">
				{#each upcomingTwoWeeks as event (event.event_id)}
					<a class="flex items-center gap-3 py-2.5 hover:text-brand" href={eventLink(event.event_id)} data-sveltekit-reset="false">
						<PortalEventDate start={event.start} />
						<span class="size-2 shrink-0 rounded-full {kindStyle(event.kind).dot}"></span>
						<span class="min-w-0 flex-1"><span class="block font-semibold text-ink">{event.title}</span><span class="block text-xs text-muted">{formatTime(event.start)}{event.location ? ' · ' + event.location : ''}</span></span>
						<span class="flex flex-wrap justify-end gap-1">
							{#each events.filter((row) => row.event_id === event.event_id) as row (row.id)}
								<span class="badge badge-sm border {statusStyle(row.status)}">{people.length > 1 ? `${firstName(row.member_id)} · ` : ''}{t(statusLabelKey(row.status))}</span>
							{/each}
						</span>
						<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
					</a>
				{/each}
			</div>
		</section>
	{/if}
{/snippet}

{#snippet attention()}
	<section class="card card-body">
		<h2 class="card-title"><ClipboardList size={16} aria-hidden="true" />{t('portal.attention')}<span class="badge badge-sm badge-error ml-auto">{attentionCount}</span></h2>
		<div class="divide-y divide-rule">
			{#each openTasks.slice(0, 4) as task (task.id)}
				<div class="flex min-h-11 items-center gap-2 py-2">
					<ListChecks size={18} class="shrink-0 text-muted" aria-hidden="true" />
					<a class="min-w-0 flex-1 hover:text-brand" href={eventLink(task.event_id)} data-sveltekit-reset="false"><span class="block font-semibold">{task.title}</span><span class="block truncate text-xs text-muted">{task.event_title} · {formatDateTime(task.start)}</span></a>
					<button type="button" class="btn btn-sm btn-primary shrink-0 min-h-11 md:h-8 md:min-h-0" disabled={!!busy} onclick={() => claim(task.event_id, task.id, true)}>{t('portal.duty_volunteer')}</button>
				</div>
			{/each}
			{#each officiatingInvites.slice(0, 4) as offer (offerKey(offer))}
				<a class="flex min-h-11 items-center gap-2 py-2 hover:text-brand" href={eventLink(offer.event_id)} data-sveltekit-reset="false">
					<Whistle size={18} class="shrink-0 text-muted" aria-hidden="true" />
					<span class="min-w-0 flex-1"><span class="block font-semibold">{offer.title}</span><span class="block text-xs text-muted">{t(`portal.crew_${offer.role}`)} · {memberName(offer.member_id)} · {formatDateTime(offer.start)}</span></span>
					<span class="badge badge-outline">{t('portal.crew_invited')}</span>
				</a>
			{/each}
			{#each needsReply.slice(0, 4) as event (event.id)}
				<a class="flex min-h-11 items-center gap-2 py-2 hover:text-brand" href={eventLink(event.event_id)} data-sveltekit-reset="false">
					<CalendarDays size={18} class="shrink-0 text-muted" aria-hidden="true" />
					<span class="min-w-0 flex-1"><span class="block font-semibold">{event.title}</span><span class="block text-xs text-muted">{formatDateTime(event.start)} · {memberName(event.member_id)}</span></span>
					<span class="badge badge-warn">{t('portal.reply')}</span>
				</a>
			{/each}
			
			{#each filteredForms as form (`${form.id}-${form.member_id}`)}
				<a class="flex min-h-11 items-center gap-2 py-2 hover:text-brand" href="/parent/forms/{form.id}/{form.member_id}">
					<ClipboardList size={18} class="shrink-0 text-muted" aria-hidden="true" />
					<span class="min-w-0 flex-1"><span class="block font-semibold">{form.title}</span><span class="block text-xs text-muted">{memberName(form.member_id)}{form.closes_at ? ' · ' + t('pubform.closes', { date: formatDateTime(form.closes_at) }) : ''}</span></span>
					<span class="badge badge-outline">{t('portal.forms')}</span>
				</a>
			{/each}
			{#each openFees as fee (`${fee.member_id}-${fee.season}`)}
				<a class="flex min-h-11 items-center gap-2 py-2 hover:text-brand" href="/parent?tab=payments">
					<Wallet size={18} class="shrink-0 text-muted" aria-hidden="true" />
					<span class="min-w-0 flex-1"><span class="block font-semibold">{t(fee.next_installment ? 'portal.membership_installment' : 'portal.payments')}</span><span class="block text-xs {fee.next_installment?.late ? 'text-danger' : 'text-muted'}">{memberName(fee.member_id)}{fee.next_installment ? ' · ' + t('portal.due_by', { when: day(fee.next_installment.due_date) }) : ' · ' + fee.season}</span></span>
					<span class="shrink-0 font-semibold">{money(fee.next_installment?.remaining ?? fee.balance)}</span>
				</a>
			{/each}
		</div>
		{#if officiatingInvites.length > 4 || needsReply.length > 4}
			<div class="flex flex-wrap justify-end gap-x-4 border-t border-rule pt-1">
				{#if officiatingInvites.length > 4}<a class="link-more" href="/parent?tab=calendar&view=list">{t('portal.crew_title')} →</a>{/if}
				{#if needsReply.length > 4}<a class="link-more" href="/parent?tab=calendar">{calendarLabel} →</a>{/if}
			</div>
		{/if}
	</section>
{/snippet}

{#snippet family()}
	{#if people.length > 1}
		<section class="card card-body">
			<h2 class="card-title"><UsersRound size={16} aria-hidden="true" />{t('portal.family')}<a class="link ml-auto text-sm font-semibold" href="/parent/profile">{t('portal.profile')} →</a></h2>
			<ul class="grid gap-3">
				{#each people.filter((person) => person.id !== data.home.viewer) as person (person.id)}
					{@const open = data.home.fees.filter((fee) => fee.member_id === person.id).reduce((sum, fee) => sum + Number(fee.balance), 0)}
					<li class="flex items-center gap-3">
						<ClubCrest name={person.name} logoUrl={person.photo_url} size={36} />
						<span class="min-w-0 flex-1"><span class="block truncate font-semibold text-ink">{person.name}</span>{#if teamsOf(person.id)}<span class="block truncate text-xs text-muted">{teamsOf(person.id)}</span>{/if}</span>
						{#if data.home.fees.some((fee) => fee.member_id === person.id)}
							<span class="badge badge-sm {open > 0 ? 'badge-warn' : 'badge-success'}">{open > 0 ? t('portal.fee_open', { amount: money(open) }) : t('portal.fee_paid')}</span>
						{/if}
					</li>
				{/each}
			</ul>
		</section>
	{/if}
{/snippet}

{#snippet sponsors()}
	{#if data.home.sponsors?.length}
		<section class="grid min-w-0 gap-2">
			<h2 class="font-mono text-[11px] tracking-[.14em] text-muted uppercase">{t('portal.partners')}</h2>
			<ul class="grid grid-cols-3 gap-2" aria-label={t('sponsors.title')}>
				{#each data.home.sponsors.slice(0, 6) as sponsor (sponsor.id)}
					<li class="card h-14 overflow-hidden">
						{#snippet logo()}
							{#if sponsor.logo_url}<img class="max-h-full max-w-full object-contain" src={sponsor.logo_url} alt={sponsor.name} loading="lazy" />{:else}<span class="text-xs font-semibold text-muted">{sponsor.name}</span>{/if}
						{/snippet}
						{#if sponsor.url && /^https?:\/\//i.test(sponsor.url)}<a class="grid size-full place-items-center p-2 hover:bg-subhead" href={sponsor.url} target="_blank" rel="noopener noreferrer">{@render logo()}</a>{:else}<div class="grid size-full place-items-center p-2">{@render logo()}</div>{/if}
					</li>
				{/each}
			</ul>
		</section>
	{/if}
{/snippet}

{#if tab !== 'home'}
	<div class="flex items-center justify-between gap-2 md:hidden">
		<h1 class="font-display text-2xl font-extrabold text-ink uppercase">{title}</h1>
		{#if tab === 'calendar' && !crewOnly}
			<button type="button" class="btn btn-outline gap-2 md:hidden" aria-expanded={monthOpen} onclick={() => (monthOpen = !monthOpen)}>
				{#if monthOpen}<List size={16} aria-hidden="true" />{t('portal.schedule')}{:else}<CalendarDays size={16} aria-hidden="true" />{t('portal.month_view')}{/if}
			</button>
		{/if}
	</div>
{/if}

{#if tab === 'calendar'}
	<div class="flex flex-wrap items-center gap-2 md:hidden">{@render viewSwitch()}</div>
	{@render peoplePicker()}
	{#if view === 'month'}
		<div class="grid min-w-0 items-start gap-4 md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
			<div class="grid gap-3 {monthOpen ? '' : 'hidden md:grid'}">
				<MonthPicker bind:month bind:selected={pickedDay} events={eventDays} />
				<p class="flex flex-wrap gap-x-4 gap-y-1 px-1 text-xs text-muted">{#each kindsPresent as kind (kind)}<span class="inline-flex items-center gap-1.5"><span class="size-2 rounded-full {kindStyle(kind).dot}"></span>{t(kindLabelKey(kind))}</span>{/each}</p>
			</div>
			<section class="card card-body min-w-0">
				<h2 class="card-title"><CalendarDays size={16} aria-hidden="true" />{pickedDay ? formatLongDay(new Date(`${pickedDay}T12:00:00`)) : t('portal.schedule')}</h2>
				<div class="divide-y divide-rule">
					{#each agenda as entry (entry.event.event_id)}{@render eventRow(entry)}{:else}<p class="py-2 text-sm text-muted">{t('portal.no_events')}</p>{/each}
				</div>
			</section>
		</div>
	{:else if view === 'week'}
		<section class="card card-body min-w-0">
			<div class="flex flex-wrap items-center gap-2">
				<h2 class="card-title"><CalendarDays size={16} aria-hidden="true" />{formatLongDay(weekDays[0])} – {formatLongDay(weekDays[6])}</h2>
				<div class="ml-auto flex gap-1">
					<button type="button" class="btn btn-outline" onclick={() => (weekStart = mondayOf(new Date()))}>{t('portal.go_today')}</button>
					<button type="button" class="btn btn-outline btn-square" aria-label={t('portal.week_previous')} onclick={() => moveWeek(-1)}><ChevronRight size={16} class="rotate-180" aria-hidden="true" /></button>
					<button type="button" class="btn btn-outline btn-square" aria-label={t('portal.week_next')} onclick={() => moveWeek(1)}><ChevronRight size={16} aria-hidden="true" /></button>
				</div>
			</div>
			<div class="grid gap-3 xl:grid-cols-7">
				{#each weekDays as day (dateKey(day))}
					{@const entries = entriesOn(day)}
					{@const today = dateKey(day) === dateKey(new Date())}
					<div class="min-w-0 rounded-box border p-2 {today ? 'border-club' : 'border-line'} {entries.length ? '' : 'hidden xl:block'}">
						<p class="mb-1 flex items-baseline gap-1.5 font-mono text-[11px] tracking-wide uppercase {today ? 'font-bold text-club-dark' : 'text-muted'}">{new Intl.DateTimeFormat(getLang(), { weekday: 'short' }).format(day)}<span class="font-display text-lg leading-none font-extrabold {today ? 'text-club-dark' : 'text-ink'}">{day.getDate()}</span></p>
						<div class="grid gap-1.5 xl:hidden">{#each entries as entry (entry.event.event_id)}{@render eventRow(entry, false)}{/each}</div>
						<div class="hidden gap-1.5 xl:grid">
							{#each entries as entry (entry.event.event_id)}
								<a class="grid gap-0.5 rounded border border-line bg-white p-1.5 text-xs hover:border-club {kindStyle(entry.event.kind).bar} border-l-4" href={eventLink(entry.event.event_id)} data-sveltekit-reset="false">
									<span class="font-mono text-muted">{formatTime(entry.event.start)}</span>
									<span class="font-semibold text-ink">{entry.event.title}</span>
									{#each entry.attendance as row (row.id)}<span class="badge badge-sm border {statusStyle(row.status)}">{people.length > 1 ? `${firstName(row.member_id)} · ` : ''}{t(statusLabelKey(row.status))}</span>{/each}
								</a>
							{/each}
						</div>
					</div>
				{/each}
			</div>
		</section>
	{:else}
		<section class="card min-w-0">
			<div class="max-h-[calc(100dvh-13rem)] overflow-y-auto px-4 pb-2" use:scrollToToday>
				{#each listGroups as group (group.key)}
					<div data-today={group.key === todayKey ? '' : undefined}>
						<h3 class="sticky top-0 z-10 flex items-center gap-2 bg-(--portal-panel) py-2 font-mono text-xs tracking-[.12em] uppercase {group.key === dateKey(new Date()) ? 'font-bold text-club-dark' : 'text-muted'}">{formatLongDay(group.date)}{#if group.key === dateKey(new Date())}<span class="badge badge-sm badge-error">{t('portal.go_today')}</span>{/if}</h3>
						<div class="divide-y divide-rule">{#each group.entries as entry (entry.event.event_id)}{@render eventRow(entry, false)}{/each}</div>
					</div>
				{:else}
					<p class="py-4 text-sm text-muted">{t('portal.no_events')}</p>
				{/each}
			</div>
		</section>
	{/if}
{:else if tab === 'news'}
	<News articles={data.home.news} />
{:else if tab === 'me'}
	<div class="grid min-w-0 items-start gap-4 md:grid-cols-[minmax(0,5fr)_minmax(0,4fr)]">
		<div class="grid gap-4">
			<section class="card card-body flex-row items-center gap-3">
				<ClubCrest name={viewer?.name ?? ''} logoUrl={viewer?.photo_url} size={48} />
				<h2 class="font-display text-xl font-extrabold text-ink uppercase">{viewer?.name}</h2>
				<div class="ml-auto"><AccountMenu email={data.auth.status === 'authenticated' ? data.auth.user.email : ''} /></div>
			</section>
			{#if people.length > 1}
				<section class="card card-body">
					<h2 class="card-title"><UsersRound size={16} aria-hidden="true" />{t('portal.family')}</h2>
					<ul class="divide-y divide-rule">
						{#each people.filter((person) => person.id !== data.home.viewer) as person (person.id)}
							<li class="flex min-h-12 items-center gap-3 py-2"><ClubCrest name={person.name} logoUrl={person.photo_url} size={32} /><span class="font-semibold">{person.name}</span></li>
						{/each}
					</ul>
				</section>
			{/if}
		</div>
		<div class="grid gap-4">
			<section class="card divide-y divide-rule overflow-hidden">
				<a class="flex min-h-12 items-center gap-2 px-4 font-semibold hover:bg-subhead" href="/parent?tab=payments"><Wallet size={17} aria-hidden="true" />{t('portal.payments')}<ChevronRight size={16} class="ml-auto text-muted" aria-hidden="true" /></a>
				<a class="flex min-h-12 items-center gap-2 px-4 font-semibold hover:bg-subhead" href="/parent?tab=forms"><ClipboardList size={17} aria-hidden="true" />{t('portal.forms')}<ChevronRight size={16} class="ml-auto text-muted" aria-hidden="true" /></a>
				<a class="flex min-h-12 items-center gap-2 px-4 font-semibold hover:bg-subhead" href="/account/security"><KeyRound size={17} aria-hidden="true" />{t('shell.menu.security')}<ChevronRight size={16} class="ml-auto text-muted" aria-hidden="true" /></a>
			</section>
			<InstallPush />
		</div>
	</div>
{:else if tab === 'forms' || tab === 'payments'}
	<a class="btn btn-outline w-fit gap-2" href="/parent?tab=me"><ArrowLeft size={16} aria-hidden="true" />{t('portal.me')}</a>
	{@render peoplePicker()}
	{#if tab === 'forms'}{@render formsList()}{:else}{@render payments()}{/if}
{:else}
	<div class="flex flex-wrap items-baseline justify-between gap-2 md:hidden">
		<h1 class="font-display text-2xl font-extrabold text-ink uppercase">{title}</h1>
		<p class="text-sm text-muted">{formatLongDay(new Date())}</p>
	</div>
	<div class="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)] xl:items-start xl:gap-5">
		<div class="grid min-w-0 content-start gap-4">
			<div class="xl:hidden">{@render peoplePicker()}</div>
			<section class="grid min-w-0 gap-3" aria-label={t('portal.next_up')}>
				{#each nextEvents as rows (rows[0].event_id)}
					{@const event = rows[0]}
					<article class="card overflow-hidden">
						<div class="grid xl:grid-cols-[1.1fr_1fr]">
							<a class="flex" href={eventLink(event.event_id)} data-sveltekit-reset="false" aria-label={event.title}><EventHero {event} {background} {crest} /></a>
							<div class="grid content-center gap-1 border-t border-sidebar-hairline bg-sidebar-hairline px-4 py-3 text-white xl:border-t-0 xl:border-l xl:px-5">
								<p class="font-mono text-[11px] tracking-[.14em] text-sidebar-fg-dim uppercase">{t('portal.who_coming')}</p>
								<div class="grid gap-x-8 md:grid-cols-2 xl:grid-cols-1">
									{#each rows as row (row.id)}{@render rsvp(row, true, true)}{/each}
								</div>
								<a class="inline-flex min-h-11 items-center text-sm font-semibold text-white hover:underline" href={eventLink(event.event_id)} data-sveltekit-reset="false">{t('portal.event_details')} →</a>
							</div>
						</div>
					</article>
				{:else}
					<div class="card card-body flex-row items-center gap-3"><CircleCheck size={20} class="text-ok" aria-hidden="true" /><p class="text-sm text-muted">{t('portal.no_events')}</p></div>
				{/each}
			</section>
			<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-1">
				{#if hasAttention}<div class="xl:hidden">{@render attention()}</div>{/if}
				{@render week()}
			</div>
			<section><News articles={data.home.news} home /></section>
			<div class="xl:hidden">{@render sponsors()}</div>
		</div>
		<aside class="hidden min-w-0 content-start gap-4 xl:grid">
			{#if hasAttention}{@render attention()}{/if}
			{@render family()}
			{@render sponsors()}
		</aside>
	</div>
{/if}

<Sheet open={!!detail} title={detail?.event.title ?? calendarLabel} onclose={() => void goto(tab === 'calendar' ? calendarLink : '/parent', { reset: false, replace: true })}>
	{#if detail}
		{@const event = detail.event}
		{@const audience = detail.attendance.length > 0}
		<div class="grid gap-4 p-3 pt-0">
			<div class="overflow-hidden rounded-box"><EventHero {event} {background} {crest} /></div>
			<div class="card card-body">
				<h2 class="card-title"><CalendarDays size={16} aria-hidden="true" />{t('portal.when_where')}</h2>
				<p class="text-sm">{formatDateTime(event.start)}{event.end ? ' – ' + formatTime(event.end) : ''}</p>
				{#if event.gathering}<p class="text-sm text-muted">{t('portal.gathering', { when: formatDateTime(event.gathering) })}</p>{/if}
				{#if event.deadline}<p class="text-sm text-muted">{t('events.deadline')} · {formatDateTime(event.deadline)}</p>{/if}
				{#if event.location}
					<div class="mt-1 flex items-center gap-3">
						<MapPin size={18} class="shrink-0 text-muted" aria-hidden="true" />
						<span class="min-w-0 flex-1 font-semibold text-ink">{event.location}</span>
						<a class="btn btn-outline btn-sm md:h-8" href="https://www.google.com/maps/search/?api=1&query={encodeURIComponent(event.location)}" target="_blank" rel="noopener noreferrer">{t('portal.open_maps')}</a>
					</div>
				{/if}
				{#if event.score_for != null && event.score_against != null}<p class="font-display text-2xl font-extrabold">{event.score_for}–{event.score_against}</p>{/if}
			</div>
			{#if audience}
				<section class="card card-body">
					<h2 class="card-title"><UserRound size={16} aria-hidden="true" />{t('portal.reply')}</h2>
					<div class="divide-y divide-rule">{#each detail.attendance as row (row.id)}{@render rsvp(row)}{/each}</div>
				</section>
			{/if}
			{#if detail.officiating.length}
				<section class="card card-body py-2">
					<div class="divide-y divide-rule">{#each detail.officiating as offer (offerKey(offer))}{@render officiating(offer)}{/each}</div>
				</section>
			{/if}
			<!-- A request to referee or officiate is only that: line-up, tasks and files are for the people invited to play. -->
			{#if audience && extras?.lineup}
				<section class="card card-body">
					<h2 class="card-title"><Users size={16} aria-hidden="true" />{t('portal.lineup')}</h2>
					<div class="flex flex-wrap gap-1.5">
						{#each extras.lineup.players as player (player.id)}
							<span class="badge border {player.mine ? 'border-club text-club-dark' : 'badge-outline'}">{player.number !== null ? `#${player.number} ` : ''}{player.name}</span>
						{/each}
					</div>
					<p class="text-xs text-muted">{t('portal.lineup_counts', { going: extras.lineup.going, out: extras.lineup.out, waiting: extras.lineup.waiting })}</p>
				</section>
			{/if}
			{#if audience && extras?.tasks.length}
				<section class="card card-body">
					<h2 class="card-title"><ListChecks size={16} aria-hidden="true" />{t('portal.duties')}</h2>
					<div class="divide-y divide-rule">
						{#each extras.tasks as task (task.id)}
							<div class="grid gap-1 py-2">
								<div class="flex items-center gap-3">
									<span class="min-w-0 flex-1">
										<span class="block font-semibold text-ink">{task.title}</span>
										<span class="block text-xs text-muted">{task.open_slots ? t('portal.task_taken', { claimed: task.claimed, needed: task.needed }) : t('portal.task_covered')}{task.claimed_by.length ? ` · ${task.claimed_by.join(', ')}` : ''}</span>
									</span>
									{#if task.claimed_by_me}
										<button class="btn btn-outline btn-sm min-h-11 md:h-8 md:min-h-0" type="button" disabled={!!busy} onclick={() => claim(event.event_id, task.id, false)}>{t('portal.task_give_back')}</button>
									{:else if task.open_slots}
										<button class="btn btn-primary btn-sm min-h-11 md:h-8 md:min-h-0" type="button" disabled={!!busy} onclick={() => claim(event.event_id, task.id, true)}>{t('portal.duty_volunteer')}</button>
									{/if}
								</div>
								{#if task.description}<p class="text-xs text-slate">{task.description}</p>{/if}
								{#if errors[task.id]}<p class="text-sm text-error" role="alert">{errors[task.id]}</p>{/if}
							</div>
						{/each}
					</div>
				</section>
			{/if}
			{#if audience && extras?.attachments.length}
				<section class="card card-body">
					<h2 class="card-title"><FileText size={16} aria-hidden="true" />{t('portal.attachments')}</h2>
					{#each extras.attachments as file (file.id)}
						<a class="flex items-center gap-3 text-sm hover:text-brand" href={file.download_url} download><FileText size={16} class="shrink-0 text-muted" aria-hidden="true" /><span class="min-w-0 flex-1 truncate font-semibold text-ink">{file.name}</span><span class="text-xs text-muted">{sizeText(file.size)}</span></a>
					{/each}
				</section>
			{/if}
		</div>
	{/if}
</Sheet>
