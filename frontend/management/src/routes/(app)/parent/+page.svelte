<script lang="ts">
	import { ArrowLeft, Bell, CalendarDays, ChevronRight, CircleCheck, ClipboardList, FileText, Handshake, KeyRound, List, UserRound, UsersRound, Wallet, Whistle } from '@lucide/svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { formatDateTime, formatLongDay, formatTime, kindStyle, statusLabelKey, statusStyle } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { OfficiatingOpportunity, PortalEvent, PortalEventDisplay } from '#lib/portal.ts';
	import { day, money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import AccountMenu from '#lib/components/AccountMenu.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import InstallPush from '#lib/components/InstallPush.svelte';
	import PersonFilter from '#lib/components/PersonFilter.svelte';
	import PortalEventDate from '#lib/components/PortalEventDate.svelte';
	import EventHero from '#lib/components/portal/EventHero.svelte';
	import MonthPicker from '#lib/components/portal/MonthPicker.svelte';
	import News from '#lib/components/portal/News.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';

	let { data } = $props();
	let selected = $state('');
	let busy = $state('');
	let notes = $state<Record<string, string>>({});
	let errors = $state<Record<string, string>>({});
	let replyOpen = $state('');
	let month = $state(new Date());
	let pickedDay = $state('');
	let monthOpen = $state(false);
	const dateKey = (date: Date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
	const tab = $derived(page.url.searchParams.get('tab') || page.url.hash.slice(1) || 'home');
	const crewOnly = $derived(tab === 'calendar' && page.url.searchParams.get('crew') === '1');
	const labels: Record<string, string> = { home: 'portal.home', calendar: 'shell.nav.calendar', news: 'shell.nav.news', me: 'portal.me', forms: 'portal.forms', payments: 'portal.payments' };
	const title = $derived(t(labels[tab] ?? labels.home));
	const people = $derived(data.home.people);
	const viewer = $derived(people.find((person) => person.id === data.home.viewer));
	const events = $derived(data.home.events.filter((event) => !selected || event.member_id === selected));
	const memberName = (id: string) => people.find((person) => person.id === id)?.name ?? '';
	const crest = $derived(data.branding?.logo_url);
	const background = $derived(data.home.event_background);
	const canReply = (event: PortalEvent) => !event.cancelled && Date.parse(event.start) > Date.now() && (event.status === 'selected' || !event.deadline || Date.parse(event.deadline) >= Date.now());
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
	const upcomingWeek = $derived.by(() => {
		const now = new Date();
		const end = new Date(now);
		end.setDate(end.getDate() + 7);
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
	const eventDays = $derived(new Set(calendar.map(({ event }) => dateKey(new Date(event.start)))));
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

	const filteredFees = $derived(data.home.fees.filter((fee) => !selected || fee.member_id === selected));
	const openFees = $derived(filteredFees.filter((fee) => Number(fee.balance) > 0));
	const filteredForms = $derived(data.home.forms.filter((form) => !selected || form.member_id === selected));
	const calendarLink = $derived(crewOnly ? '/parent?tab=calendar&crew=1' : '/parent?tab=calendar');
	const eventLink = (id: string) => tab === 'calendar' ? `${calendarLink}&event=${id}` : `/parent?event=${id}`;

	usePageHeader(() => ({ title }));

	async function reply(event: PortalEvent, status: string) {
		if (busy) return;
		busy = event.id;
		errors[event.id] = '';
		try {
			const result = await api<PortalEvent>('POST', `/portal/attendance/${event.id}`, { body: { status, note: notes[event.id] ?? event.note } });
			if (result.ok) {
				await invalidateAll();
				delete notes[event.id];
				replyOpen = '';
			} else {
				errors[event.id] = result.code === 'late_installment' ? t('portal.late') : (result.fields.note ?? result.message ?? t('common.error.generic'));
			}
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

{#snippet rsvp(event: PortalEvent, quick = false)}
	<div class="grid gap-2 py-1.5">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<div class="min-w-0 flex-1">
				<p class="text-sm font-semibold">{memberName(event.member_id)}</p>
				{#if quick && canReply(event) && !going(event) && event.deadline}<p class="text-xs text-muted">{t('portal.reply_by', { when: formatDateTime(event.deadline) })}</p>{/if}
			</div>
			{#if event.cancelled || !canReply(event) || going(event)}
				<span class="badge badge-sm border {event.cancelled ? 'badge-error' : statusStyle(event.status)}">{event.cancelled ? t('portal.cancelled') : t(statusLabelKey(event.status))}</span>
			{/if}
			{#if canReply(event)}
				<div class="flex flex-wrap gap-1.5">
					{#if !going(event)}
						<button type="button" class="btn btn-sm btn-primary min-h-11" disabled={!!busy} onclick={() => reply(event, 'present')}>{t('portal.going')}</button>
						{#if !quick}<button type="button" class="btn btn-sm btn-outline min-h-11" disabled={!!busy} onclick={() => reply(event, 'maybe')}>{t('portal.maybe')}</button>{/if}
					{/if}
					<button type="button" class="btn btn-sm btn-outline min-h-11" disabled={!!busy} aria-expanded={replyOpen === event.id} onclick={() => (replyOpen = replyOpen === event.id ? '' : event.id)}>{t(going(event) ? 'portal.cant_make_it' : 'portal.cant_go')}</button>
				</div>
			{/if}
		</div>
		{#if canReply(event) && replyOpen === event.id}
			<form class="grid gap-2 rounded-box border border-line bg-subhead p-3" onsubmit={(e) => { e.preventDefault(); void reply(event, event.status === 'selected' ? 'dropout' : 'absent'); }}>
				<label class="grid gap-1 text-sm">{t('portal.reason')}
					<textarea class="textarea textarea-bordered w-full text-ink" rows="2" maxlength="2000" value={notes[event.id] ?? event.note} oninput={(e) => (notes[event.id] = e.currentTarget.value)}></textarea>
				</label>
				<div class="flex flex-wrap justify-end gap-2">
					<button class="btn btn-sm btn-outline min-h-11" type="button" onclick={() => (replyOpen = '')}>{t('common.cancel')}</button>
					<button class="btn btn-sm btn-primary min-h-11" type="submit" disabled={!!busy}>{t('common.save')}</button>
				</div>
			</form>
		{/if}
		{#if errors[event.id]}<p class="text-sm text-error" role="alert">{errors[event.id]}</p>{/if}
	</div>
{/snippet}

{#snippet officiating(offer: OfficiatingOpportunity)}
	<div class="grid gap-1.5 py-2">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<span class="flex min-w-0 flex-1 items-center gap-2 text-sm font-semibold"><Whistle size={15} class="shrink-0" aria-hidden="true" />{t(`portal.crew_${offer.role}`)} · {memberName(offer.member_id)}</span>
			<span class="badge badge-sm {offer.status === 'assigned' ? 'badge-success' : 'badge-outline'}" role="status">{t(offer.status === 'invited' && !offer.signup_open ? 'portal.crew_closed' : `portal.crew_${offer.status}`)}</span>
		</div>
		{#if offer.signup_open && offer.status !== 'assigned'}
			{#if offer.deadline}<p class="text-xs text-muted">{t('portal.reply_by', { when: formatDateTime(offer.deadline) })}</p>{/if}
			<div class="flex flex-wrap gap-2">
				{#if offer.status !== 'signed_up'}<button type="button" class="btn btn-sm btn-primary min-h-11" disabled={!!busy} onclick={() => officiate(offer, true)}>{t('portal.crew_volunteer')}</button>{/if}
				{#if offer.status !== 'declined'}<button type="button" class="btn btn-sm btn-outline min-h-11" disabled={!!busy} onclick={() => officiate(offer, false)}>{t(offer.status === 'signed_up' ? 'portal.crew_withdraw' : 'portal.crew_decline')}</button>{/if}
			</div>
		{/if}
		{#if errors[offerKey(offer)]}<p role="alert" class="text-sm text-error">{errors[offerKey(offer)]}</p>{/if}
	</div>
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
	{@render peoplePicker()}
	<div class="grid min-w-0 items-start gap-4 {!crewOnly ? 'md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]' : ''}">
		{#if !crewOnly}<div class={monthOpen ? '' : 'hidden md:block'}><MonthPicker bind:month bind:selected={pickedDay} events={eventDays} /></div>{/if}
		<section class="card card-body min-w-0">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h2 class="card-title">
					{#if crewOnly}<Whistle size={16} aria-hidden="true" />{t('portal.crew_title')}{:else}<CalendarDays size={16} aria-hidden="true" />{t('portal.schedule')}{/if}
				</h2>
				{#if crewOnly}<a class="btn btn-outline btn-sm min-h-11" href="/parent?tab=calendar">{t('portal.calendar')}</a>{/if}
			</div>
			<div class="divide-y divide-rule">
				{#each agenda as entry (entry.event.event_id)}
					{@const event = entry.event}
					<div class="flex gap-3 py-3">
						<PortalEventDate start={event.start} />
						<div class="min-w-0 flex-1">
							<a class="flex min-h-10 items-start gap-2 hover:text-brand" href={eventLink(event.event_id)} data-sveltekit-reset="false">
								<span class="mt-2 size-2 shrink-0 rounded-full {kindStyle(event.kind).dot}"></span>
								<span class="min-w-0 flex-1">
									<span class="block font-semibold text-ink">{event.title}</span>
									<span class="block text-xs text-muted">{formatTime(event.start)}{event.location ? ' · ' + event.location : ''}</span>
								</span>
								{#if event.score_for != null && event.score_against != null}<span class="font-mono text-sm font-bold">{event.score_for}–{event.score_against}</span>{:else}<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />{/if}
							</a>
							{#each entry.attendance as row (row.id)}{@render rsvp(row)}{/each}
							{#each entry.officiating as offer (offerKey(offer))}{@render officiating(offer)}{/each}
						</div>
					</div>
				{:else}
					<p class="py-2 text-sm text-muted">{t('portal.no_events')}</p>
				{/each}
			</div>
		</section>
	</div>
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
	{@render peoplePicker()}
	<div class="grid min-w-0 items-start gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
		<div class="grid min-w-0 gap-4">
			<section class="grid min-w-0 gap-3" aria-label={t('portal.next_up')}>
				{#each nextEvents as rows (rows[0].event_id)}
					{@const event = rows[0]}
					<article class="card overflow-hidden">
						<h2 class="card-title px-4 py-3"><CalendarDays size={16} aria-hidden="true" />{t('portal.next_up')}</h2>
						<a class="block" href={eventLink(event.event_id)} data-sveltekit-reset="false"><EventHero {event} {background} {crest} /></a>
						<div class="divide-y divide-rule px-4 py-2">
							{#each rows as row (row.id)}{@render rsvp(row, true)}{/each}
						</div>
					</article>
				{:else}
					<div class="card card-body flex-row items-center gap-3"><CircleCheck size={20} class="text-ok" aria-hidden="true" /><p class="text-sm text-muted">{t('portal.no_events')}</p></div>
				{/each}
			</section>
			{#if upcomingWeek.length}
				<section class="card card-body">
					<h2 class="card-title"><CalendarDays size={16} aria-hidden="true" />{t('portal.next_seven_days')}</h2>
					<div class="divide-y divide-rule">
						{#each upcomingWeek as event (event.event_id)}
							<a class="flex items-center gap-3 py-2 hover:text-brand" href={eventLink(event.event_id)} data-sveltekit-reset="false">
								<PortalEventDate start={event.start} />
								<span class="min-w-0 flex-1"><span class="block font-semibold">{event.title}</span><span class="block text-xs text-muted">{formatTime(event.start)}{event.location ? ' · ' + event.location : ''}</span></span>
								<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
							</a>
						{/each}
					</div>
				</section>
			{/if}
		</div>
		<div class="grid min-w-0 gap-4">
			{#if needsReply.length || filteredForms.length || openFees.length || officiatingInvites.length}
				<section class="card card-body">
					<h2 class="card-title"><ClipboardList size={16} aria-hidden="true" />{t('portal.attention')}</h2>
					<div class="divide-y divide-rule">
						{#each officiatingInvites.slice(0, 4) as offer (offerKey(offer))}
							<a class="flex min-h-11 items-center gap-2 py-2 hover:text-brand" href={eventLink(offer.event_id)} data-sveltekit-reset="false">
								<Whistle size={16} class="shrink-0" aria-hidden="true" />
								<span class="min-w-0 flex-1"><span class="block font-semibold">{offer.title}</span><span class="block text-xs text-muted">{t(`portal.crew_${offer.role}`)} · {memberName(offer.member_id)} · {formatDateTime(offer.start)}</span></span>
								<span class="badge badge-outline">{t('portal.crew_invited')}</span>
							</a>
						{/each}

						{#each needsReply.slice(0, 4) as event (event.id)}
							<a class="flex min-h-11 items-center gap-2 py-2 hover:text-brand" href={eventLink(event.event_id)} data-sveltekit-reset="false">
								<span class="min-w-0 flex-1"><span class="block font-semibold">{event.title}</span><span class="block text-xs text-muted">{formatDateTime(event.start)} · {memberName(event.member_id)}</span></span>
								<span class="badge badge-warn">{t('portal.reply')}</span>
							</a>
						{/each}
						{#if officiatingInvites.length > 4}<a class="link flex min-h-11 items-center text-sm font-semibold" href="/parent?tab=calendar&crew=1">{t('portal.crew_title')}</a>{/if}
						{#if needsReply.length > 4}<a class="link flex min-h-11 items-center text-sm font-semibold" href="/parent?tab=calendar">{t('portal.calendar')}</a>{/if}
						{#each filteredForms as form (`${form.id}-${form.member_id}`)}
							<a class="flex min-h-11 items-center gap-2 py-2 hover:text-brand" href="/parent/forms/{form.id}/{form.member_id}">
								<span class="min-w-0 flex-1"><span class="block font-semibold">{form.title}</span><span class="block text-xs text-muted">{memberName(form.member_id)}{form.closes_at ? ' · ' + t('pubform.closes', { date: formatDateTime(form.closes_at) }) : ''}</span></span>
								<span class="badge badge-outline">{t('portal.forms')}</span>
							</a>
						{/each}
						{#each openFees as fee (`${fee.member_id}-${fee.season}`)}
							<a class="flex min-h-11 items-center gap-2 py-2 hover:text-brand" href="/parent?tab=payments">
								<span class="min-w-0 flex-1"><span class="block font-semibold">{t(fee.next_installment ? 'portal.membership_installment' : 'portal.payments')}</span><span class="block text-xs {fee.next_installment?.late ? 'text-danger' : 'text-muted'}">{memberName(fee.member_id)}{fee.next_installment ? ' · ' + t('portal.due_by', { when: day(fee.next_installment.due_date) }) : ' · ' + fee.season}</span></span>
								<span class="shrink-0 font-semibold">{money(fee.next_installment?.remaining ?? fee.balance)}</span>
							</a>
						{/each}
					</div>
				</section>
			{/if}
			<section><News articles={data.home.news} home /></section>
			{#if data.home.sponsors?.length}
				<section class="grid min-w-0 gap-2">
					<h2 class="card-title"><Handshake size={16} aria-hidden="true" />{t('sponsors.title')}</h2>
					<ul class="flex max-w-full snap-x gap-2 overflow-x-auto pb-1" aria-label={t('sponsors.title')}>
						{#each data.home.sponsors as sponsor (sponsor.id)}
							<li class="card h-14 w-28 shrink-0 snap-start overflow-hidden">
								{#snippet logo()}
									{#if sponsor.logo_url}<img class="max-h-full max-w-full object-contain" src={sponsor.logo_url} alt={sponsor.name} loading="lazy" />{:else}<span class="text-xs font-semibold text-muted">{sponsor.name}</span>{/if}
								{/snippet}
								{#if sponsor.url && /^https?:\/\//i.test(sponsor.url)}<a class="grid size-full place-items-center p-2 hover:bg-subhead" href={sponsor.url} target="_blank" rel="noopener noreferrer">{@render logo()}</a>{:else}<div class="grid size-full place-items-center p-2">{@render logo()}</div>{/if}
							</li>
						{/each}
					</ul>
				</section>
			{/if}
		</div>
	</div>
{/if}

<Sheet open={!!detail} title={detail?.event.title ?? t('portal.calendar')} onclose={() => void goto(tab === 'calendar' ? calendarLink : '/parent', { reset: false, replace: true })}>
	{#if detail}
		{@const event = detail.event}
		<div class="grid gap-4 p-3 pt-0">
			<div class="overflow-hidden rounded-box"><EventHero {event} {background} {crest} /></div>
			<div class="card card-body">
				<h2 class="card-title"><CalendarDays size={16} aria-hidden="true" />{event.title}</h2>
				<p class="text-sm">{formatDateTime(event.start)}{event.end ? ' – ' + formatTime(event.end) : ''}</p>
				{#if event.gathering}<p class="text-sm text-muted">{t('portal.gathering', { when: formatDateTime(event.gathering) })}</p>{/if}
				{#if event.deadline}<p class="text-sm text-muted">{t('events.deadline')} · {formatDateTime(event.deadline)}</p>{/if}
				{#if event.score_for != null && event.score_against != null}<p class="font-display text-2xl font-extrabold">{event.score_for}–{event.score_against}</p>{/if}
			</div>
			{#if detail.attendance.length}
				<section class="card card-body">
					<h2 class="card-title"><UserRound size={16} aria-hidden="true" />{t('portal.reply')}</h2>
					<div class="divide-y divide-rule">{#each detail.attendance as row (row.id)}{@render rsvp(row)}{/each}</div>
				</section>
			{/if}
			{#if detail.officiating.length}
				<section class="card card-body">
					<h2 class="card-title"><Whistle size={16} aria-hidden="true" />{t('portal.crew_title')}</h2>
					<div class="divide-y divide-rule">{#each detail.officiating as offer (offerKey(offer))}{@render officiating(offer)}{/each}</div>
				</section>
			{/if}
		</div>
	{/if}
</Sheet>
