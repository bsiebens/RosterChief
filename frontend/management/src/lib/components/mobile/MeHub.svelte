<script lang="ts">
	import { onMount } from 'svelte';
	import { CalendarDays } from '@lucide/svelte';
	import { api } from '#lib/management-api.ts';
	import { ArrowRightLeft, Bell, ChartColumn, ChevronRight, IdCard, Ticket, ClipboardList, FileText, KeyRound, Shirt, UserRound, UsersRound, Wallet, Whistle, ReceiptText } from '@lucide/svelte';
	import AccountMenu from '#lib/components/AccountMenu.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import InstallPush from '#lib/components/InstallPush.svelte';
	import ThemeSwitch from '#lib/components/portal/ThemeSwitch.svelte';
	import { rememberApp } from '#lib/landing.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * Me on a phone: who I am, a switch to the coach view for people who coach, then three short groups: what I still have to do, the club
	 * (shop, following) and my settings (family, notifications, appearance). Counts show only when something is waiting.
	 */
	let {
		viewer,
		people,
		email,
		counts,
		shop,
		coach
	}: {
		viewer: { name: string; photo_url: string | null } | undefined;
		people: { id: string; name: string; first_name: string }[];
		email: string;
		counts: { documents: number; payments: number; forms: number };
		shop: boolean;
		/** The person also has a coach or management side. */
		coach: boolean;
	} = $props();

	// "Switch club" is only offered to someone who belongs to more than one club.
	let manyClubs = $state(false);
	onMount(async () => {
		const result = await api<unknown[]>('GET', '/my-clubs');
		manyClubs = !!(result.ok && result.data && result.data.length > 1);
	});

	const children = $derived(people.slice(1).map((person) => person.first_name || person.name));
	const todo = $derived([
		{ key: 'documents', href: '/parent?tab=documents', icon: FileText, label: t('portal.tile_documents'), n: counts.documents, tone: 'bg-warn-bg text-warn-text', hint: counts.documents ? t('portal.me_docs_open', { n: counts.documents }) : t('portal.me_all_good') },
		{ key: 'payments', href: '/parent?tab=payments', icon: Wallet, label: t('portal.tile_payments'), n: counts.payments, tone: 'bg-info-bg text-info-text', hint: counts.payments ? t('portal.me_pay_open', { n: counts.payments }) : t('portal.me_all_good') },
		{ key: 'forms', href: '/parent/forms', icon: ClipboardList, label: t('portal.forms_evaluations'), n: counts.forms, tone: 'bg-danger-bg text-danger', hint: counts.forms ? t('portal.me_forms_open', { n: counts.forms }) : t('portal.me_all_good') }
	]);
	const club = $derived([
		...(shop ? [{ key: 'orders', href: '/parent/shop?view=orders', icon: ReceiptText, label: t('portal.shop.orders'), hint: '' }] : []),
		{ key: 'memberships', href: '/parent/profile?view=memberships', icon: IdCard, label: t('portal.mem_title'), hint: '' },
		{ key: 'vouchers', href: '/parent/profile?view=vouchers', icon: Ticket, label: t('portal.vouchers'), hint: '' },
		{ key: 'stats', href: '/parent/stats', icon: ChartColumn, label: t('portal.stats_page'), hint: '' },
		{ key: 'teams', href: '/parent/profile?view=follow', icon: Shirt, label: t('portal.follow_title'), hint: '' }
	]);
	const settings = $derived([
		{ key: 'profile', href: '/parent/profile', icon: UserRound, label: 'portal.profile_details' },
		...(people.length > 1 ? [{ key: 'family', href: '/parent/profile', icon: UsersRound, label: 'portal.family' }] : []),
		{ key: 'notifications', href: '/parent/notifications?view=settings', icon: Bell, label: 'portal.notifications' },
		{ key: 'calendar-sync', href: '/parent/calendar-sync', icon: CalendarDays, label: 'portal.sync_calendar' },
		{ key: 'security', href: '/account/security', icon: KeyRound, label: 'shell.menu.security' },
		...(manyClubs ? [{ key: 'clubs', href: '/parent/profile?view=clubs', icon: ArrowRightLeft, label: 'portal.switch_club' }] : [])
	]);
</script>

<div class="grid min-w-0 gap-5">
	<section class="card card-body flex-row items-center gap-3">
		<ClubCrest name={viewer?.name ?? ''} logoUrl={viewer?.photo_url} size={52} />
		<div class="min-w-0 flex-1">
			<h2 class="truncate font-display text-xl leading-tight font-extrabold text-ink uppercase">{viewer?.name}</h2>
			{#if children.length}<p class="truncate text-sm text-muted">{t('portal.me_parent_of', { names: children.join(', ') })}</p>{/if}
		</div>
		<AccountMenu {email} />
	</section>

	{#if coach}
		<a class="flex min-h-18 items-center gap-3 rounded-box border border-sidebar-hairline bg-sidebar-bg px-4 py-3 text-sidebar-fg" href="/coach" onclick={() => rememberApp('manage')}>
			<span class="grid size-10 shrink-0 place-items-center rounded-box bg-club text-club-content"><Whistle size={20} aria-hidden="true" /></span>
			<span class="min-w-0 flex-1"><b class="block">{t('portal.switch_coach')}</b><span class="block text-sm text-sidebar-fg-dim">{t('portal.switch_coach_hint')}</span></span>
			<ChevronRight size={18} class="shrink-0 opacity-70" aria-hidden="true" />
		</a>
	{/if}

	<section class="grid gap-2">
		<h2 class="font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.me_todo')}</h2>
		<div class="card divide-y divide-rule overflow-hidden">
			{#each todo as row (row.key)}
				<a class="flex min-h-16 items-center gap-3 px-4 py-2" href={row.href}>
					<span class="grid size-10 shrink-0 place-items-center rounded-box {row.n ? row.tone : 'bg-ok-bg text-ok-text'}"><row.icon size={18} aria-hidden="true" /></span>
					<span class="min-w-0 flex-1"><b class="block text-ink">{row.label}</b><span class="block truncate text-sm {row.n ? 'text-ink' : 'text-muted'}">{row.hint}</span></span>
					{#if row.n}<span class="grid min-w-5 place-items-center rounded-full bg-club px-1.5 text-xs leading-5 font-bold text-club-content">{row.n}</span>{/if}
					<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />
				</a>
			{/each}
		</div>
	</section>

	<section class="grid gap-2">
		<h2 class="font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.me_club')}</h2>
		<div class="card divide-y divide-rule overflow-hidden">
			{#each club as row (row.key)}
				<a class="flex min-h-14 items-center gap-3 px-4" href={row.href}><span class="grid size-10 shrink-0 place-items-center rounded-box bg-subhead text-ink"><row.icon size={18} aria-hidden="true" /></span><b class="min-w-0 flex-1 text-ink">{row.label}</b><ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" /></a>
			{/each}
		</div>
	</section>

	<section class="grid gap-2">
		<h2 class="font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{t('portal.me_settings')}</h2>
		<div class="card divide-y divide-rule overflow-hidden">
			{#each settings as row (row.key)}
				<a class="flex min-h-14 items-center gap-3 px-4" href={row.href}><span class="grid size-10 shrink-0 place-items-center rounded-box bg-subhead text-ink"><row.icon size={18} aria-hidden="true" /></span><b class="min-w-0 flex-1 text-ink">{t(row.label)}</b><ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" /></a>
				
			{/each}
			<div class="p-4"><ThemeSwitch /></div>
		</div>
	</section>

	<InstallPush />
</div>
