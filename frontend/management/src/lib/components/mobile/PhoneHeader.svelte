<script lang="ts">
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { ArrowLeft, ArrowLeftRight, Bell, House, Whistle } from '@lucide/svelte';
	import { page } from '$app/state';
	import NotificationBell from '#lib/components/NotificationBell.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import { rememberApp } from '#lib/landing.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { notifications } from '#lib/notifications.svelte.ts';

	/**
	 * The header of the member and coach apps on a phone, as on the design boards: the page title with a small mono line above it, the page's own
	 * chip (person or team), and the bell. A sub page gets a round back button. The coach side is a near-black band with a hanging "Coach view" tag
	 * and a red switch button (member view / coach view).
	 */
	let { mode, title, context = '', actions = null, canSwitch = false }: { mode: 'parent' | 'coach'; title: string; context?: string; actions?: Snippet | null; canSwitch?: boolean } = $props();

	onMount(() => void notifications.load());
	let switchOpen = $state(false);

	// Where the round back button leads: Me for everything that lives behind it, the coach home for coach sub pages.
	const back = $derived.by(() => {
		const path = page.url.pathname;
		const tab = page.url.searchParams.get('tab');
		if (mode === 'parent') {
			if (path.startsWith('/member/notifications')) return tab === null && page.url.searchParams.get('view') !== 'settings' ? '/member' : '/member?tab=me';
			if (path.startsWith('/member/shop')) return tab === null && page.url.searchParams.get('view') === 'orders' ? '/member?tab=me' : null;
			if (path !== '/member') return '/member?tab=me';
			if (tab === 'news' && page.url.searchParams.get('article')) return '/member?tab=news';
			return tab === 'payments' || tab === 'documents' ? '/member?tab=me' : null;
		}
		if (path.startsWith('/coach/notifications')) return '/coach';
		if (path.startsWith('/coach/news/')) return '/coach?tab=news';
		if (path.startsWith('/calendar/events/')) {
			// Back to the coach calendar on the team the event belongs to and the month it is in.
			const event = page.data.event as { teams?: { id: string }[]; start?: string } | undefined;
			if (!event?.start) return '/coach?tab=calendar';
			const d = new Date(event.start);
			const month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
			return `/coach?tab=calendar&team=${(event.teams ?? []).map((team) => team.id).join(',')}&month=${month}`;
		}
		return null;
	});
</script>

<div class="phone-top md:hidden {mode === 'coach' ? 'coach' : ''}">
	{#if mode === 'coach'}<span class="phone-tag">{t('portal.coach_view')}</span>{/if}
	{#if back}<a class="phone-ib" href={back} aria-label={t('common.back')}><ArrowLeft size={22} aria-hidden="true" /></a>{/if}
	<h1 class="phone-title" class:long={title.length > 14}>{#if context}<span class="phone-sub">{context}</span>{/if}{title}</h1>
	{#if actions}{@render actions()}{/if}
	{#if mode === 'parent' || mode === 'coach'}
		<a class="phone-ib" href="/{mode === 'parent' ? 'member' : 'coach'}/notifications" aria-label={notifications.unread ? t('portal.todo_count', { n: notifications.unread }) : t('portal.notifications')}>
			<Bell size={22} aria-hidden="true" />{#if notifications.unread > 0}<span class="phone-dot"></span>{/if}
		</a>
	{/if}
	{#if mode === 'coach'}
		{#if canSwitch}<button type="button" class="phone-switch" aria-label={t('portal.switch_view')} onclick={() => (switchOpen = true)}><ArrowLeftRight size={20} aria-hidden="true" /></button>{/if}
	{/if}
</div>

{#if mode === 'coach' && canSwitch}
	<Sheet open={switchOpen} title={t('portal.switch_view')} onclose={() => (switchOpen = false)}>
		<div class="grid gap-3 p-4 pt-0">
			<p class="text-sm text-muted">{t('portal.switch_view_help')}</p>
			<a class="card flex min-h-20 items-center gap-3 p-4" href="/member" onclick={() => rememberApp('member')}>
				<span class="grid size-12 shrink-0 place-items-center rounded-box bg-subhead text-ink"><House size={22} aria-hidden="true" /></span>
				<span class="min-w-0 flex-1"><b class="block text-lg text-ink">{t('portal.member_view')}</b><span class="block text-sm text-muted">{t('portal.member_view_hint')}</span></span>
			</a>
			<div class="card flex min-h-20 items-center gap-3 border-2 border-club p-4">
				<span class="grid size-12 shrink-0 place-items-center rounded-box bg-club text-club-content"><Whistle size={22} aria-hidden="true" /></span>
				<span class="min-w-0 flex-1"><b class="block text-lg text-ink">{t('portal.coach_view')}</b><span class="block text-sm text-muted">{t('portal.coach_view_hint')}</span></span>
				<span class="badge badge-success">{t('portal.now')}</span>
			</div>
		</div>
	</Sheet>
{/if}
