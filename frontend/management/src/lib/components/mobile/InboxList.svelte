<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { Bell, CheckCheck, ChevronRight } from '@lucide/svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { notificationHref, notifications, type AppNotification } from '#lib/notifications.svelte.ts';

	/**
	 * The notification inbox as a page on a phone: today first, then earlier, unread ones with a red dot. Tapping one marks it read and opens
	 * what it is about inside the member app.
	 */
	let { portal = 'parent' }: { portal?: 'parent' | 'coach' } = $props();
	onMount(() => void notifications.load());

	const startOfToday = () => new Date(new Date().getFullYear(), new Date().getMonth(), new Date().getDate()).getTime();
	const today = $derived(notifications.items.filter((n) => Date.parse(n.created) >= startOfToday()));
	const earlier = $derived(notifications.items.filter((n) => Date.parse(n.created) < startOfToday()));
	const stamp = (iso: string) => new Intl.DateTimeFormat(getLang(), Date.parse(iso) >= startOfToday() ? { hour: '2-digit', minute: '2-digit' } : { day: 'numeric', month: 'short' }).format(new Date(iso));

	async function open(n: AppNotification) {
		if (!n.read) void notifications.markRead(n.id);
		if (n.href) await goto(notificationHref(n.href, portal));
	}
</script>

{#snippet group(title: string, rows: AppNotification[])}
	{#if rows.length}
		<section class="grid gap-2">
			<h2 class="font-display text-lg font-extrabold tracking-[.02em] text-ink uppercase">{title}</h2>
			<div class="card divide-y divide-rule overflow-hidden">
				{#each rows as n (n.id)}
					<button type="button" class="flex min-h-16 w-full items-center gap-3 px-4 py-2 text-left" onclick={() => open(n)}>
						<span class="size-2.5 shrink-0 rounded-full {n.read ? 'bg-transparent' : 'bg-club'}" aria-label={n.read ? undefined : t('portal.unread')}></span>
						<span class="min-w-0 flex-1"><b class="block truncate text-ink {n.read ? 'font-semibold' : ''}">{n.title}</b><span class="block truncate text-sm text-muted">{n.body || stamp(n.created)}</span></span>
						<span class="shrink-0 font-mono text-[11px] text-muted">{stamp(n.created)}</span>
						{#if n.href}<ChevronRight size={16} class="shrink-0 text-muted" aria-hidden="true" />{/if}
					</button>
				{/each}
			</div>
		</section>
	{/if}
{/snippet}

<div class="grid gap-4 md:hidden">
	{#if notifications.items.length}
		<div class="flex justify-end">{#if notifications.unread > 0}<button type="button" class="btn btn-outline btn-sm gap-1.5" onclick={() => notifications.markAllRead()}><CheckCheck size={14} aria-hidden="true" />{t('portal.mark_all_read')}</button>{/if}</div>
		{@render group(t('portal.inbox_today'), today)}
		{@render group(t('portal.inbox_earlier'), earlier)}
	{:else}
		<div class="card card-body items-center gap-2 py-8 text-center text-muted"><Bell size={24} aria-hidden="true" /><p class="text-sm">{t('portal.inbox_empty')}</p></div>
	{/if}
</div>
