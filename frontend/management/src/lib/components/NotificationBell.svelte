<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { Bell, BellOff, CheckCheck, CircleAlert, CircleCheck, Info, Trash2 } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { notificationHref as hrefFor, notifications, type AppNotification } from '#lib/notifications.svelte.ts';

	let { dark = false, portal }: { dark?: boolean; portal?: 'parent' | 'coach' } = $props();
	const panelId = $props.id();
	let open = $state(false);
	let root: HTMLDivElement | undefined = $state();

	// Loaded in the browser only: the relative times ("12 minutes ago") would differ between server and client otherwise.
	onMount(() => {
		void notifications.load();
		// New notifications show up without a reload: every minute, and when the tab is looked at again.
		const timer = setInterval(() => void notifications.load(), 60_000);
		const onVisible = () => document.visibilityState === 'visible' && void notifications.load();
		document.addEventListener('visibilitychange', onVisible);
		return () => {
			clearInterval(timer);
			document.removeEventListener('visibilitychange', onVisible);
		};
	});

	const icons = { info: Info, success: CircleCheck, warning: CircleAlert };
	const tint = { info: 'text-info-text bg-info-bg', success: 'text-ok-text bg-ok-bg', warning: 'text-warn-text bg-warn-bg' };

	function ago(iso: string): string {
		const seconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
		const rtf = new Intl.RelativeTimeFormat(getLang(), { numeric: 'auto' });
		for (const [unit, size] of [['day', 86400], ['hour', 3600], ['minute', 60]] as const) {
			if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
		}
		return rtf.format(0, 'second');
	}

	async function openItem(n: AppNotification) {
		if (!n.read) void notifications.markRead(n.id);
		if (n.href) {
			open = false;
			await goto(notificationHref(n.href));
		}
	}

	const notificationHref = (href: string) => hrefFor(href, portal);

	function onWindowClick(e: MouseEvent) {
		if (open && root && !root.contains(e.target as Node)) open = false;
	}
</script>

<svelte:window onclick={onWindowClick} onkeydown={(e) => e.key === 'Escape' && (open = false)} />

<div class="relative" bind:this={root}>
	<button
		type="button"
		class="btn btn-square relative {dark ? 'btn-ghost border-sidebar-hairline text-sidebar-fg! hover:bg-sidebar-hairline' : 'btn-outline'}"
		aria-label={notifications.unread ? t('notifications.bell_unread', { n: notifications.unread }) : t('notifications.title')}
		aria-expanded={open}
		aria-controls={open ? panelId : undefined}
		onclick={() => (open = !open)}>
		<Bell size={18} aria-hidden="true" />
		{#if notifications.unread}
			<span class="absolute -top-1.5 -right-1.5 grid h-5 min-w-5 place-items-center rounded-full bg-club px-1 font-mono text-[10px] leading-none font-bold text-club-content ring-2 ring-white">{notifications.unread > 9 ? '9+' : notifications.unread}</span>
		{/if}
	</button>

	{#if open}
		<div id={panelId} class="absolute top-full right-0 z-30 mt-2 w-[26rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-line bg-white text-slate shadow-xl" role="region" aria-label={t('notifications.title')}>
			<div class="flex items-center justify-between gap-2 border-b border-line bg-white px-4 py-3">
				<h2 class="font-display text-base font-extrabold tracking-[.04em] text-ink uppercase flex items-center gap-2"><Bell size={16} class="shrink-0" aria-hidden="true" /> {t('notifications.title')}</h2>
				<div class="flex items-center gap-1.5">
					<button class="btn btn-outline btn-xs gap-1" type="button" disabled={!notifications.unread} onclick={() => notifications.markAllRead()}><CheckCheck size={13} aria-hidden="true" /> {t('notifications.read_all')}</button>
					<button class="btn btn-outline btn-error btn-xs gap-1" type="button" disabled={!notifications.items.length} onclick={() => notifications.clearAll()}><Trash2 size={13} aria-hidden="true" /> {t('notifications.clear_all')}</button>
				</div>
			</div>

			{#if notifications.items.length}
				<ul class="max-h-[24rem] divide-y divide-rule overflow-y-auto bg-white">
					{#each notifications.items as n (n.id)}
						{@const Icon = icons[n.kind]}
						<li>
							<button type="button" class="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-surface {n.read ? '' : 'bg-club/5'}" onclick={() => openItem(n)}>
								<span class="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full {tint[n.kind]}"><Icon size={16} aria-hidden="true" /></span>
								<span class="min-w-0 flex-1">
									<span class="flex items-start justify-between gap-2">
										<span class="text-sm {n.read ? 'font-medium text-slate' : 'font-bold text-ink'}">{n.title}</span>
										{#if !n.read}<span class="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-club" aria-label={t('notifications.unread')}></span>{/if}
									</span>
									<span class="mt-0.5 block text-sm text-muted">{n.body}</span>
									<span class="mt-1 block font-mono text-[11px] text-dim">{ago(n.created)}</span>
								</span>
							</button>
						</li>
					{/each}
				</ul>
			{:else}
				<div class="grid place-items-center gap-2 bg-white px-4 py-10 text-center text-muted">
					<BellOff size={26} aria-hidden="true" />
					<p class="text-sm">{t('notifications.empty')}</p>
				</div>
			{/if}
		</div>
	{/if}
</div>
