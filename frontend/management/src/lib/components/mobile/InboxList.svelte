<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { Bell, CheckCheck, ChevronRight, Trash2 } from '@lucide/svelte';
	import InstallPush from '#lib/components/InstallPush.svelte';
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

	// Deleting everything asks once, right on the button.
	let confirmClear = $state(false);
	async function clear() {
		confirmClear = false;
		await notifications.clearAll();
	}

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
					<button type="button" class="flex min-h-16 w-full items-start gap-3 px-4 py-3 text-left" onclick={() => open(n)}>
						<span class="mt-1.5 size-2.5 shrink-0 rounded-full {n.read ? 'bg-transparent' : 'bg-club'}" aria-label={n.read ? undefined : t('portal.unread')}></span>
						<span class="min-w-0 flex-1"><b class="block text-ink [overflow-wrap:anywhere] {n.read ? 'font-semibold' : ''}">{n.title}</b>{#if n.body}<span class="mt-0.5 block text-sm whitespace-pre-line text-muted [overflow-wrap:anywhere]">{n.body}</span>{/if}</span>
						<span class="mt-0.5 shrink-0 font-mono text-[11px] text-muted">{stamp(n.created)}</span>
						{#if n.href}<ChevronRight size={16} class="mt-0.5 shrink-0 text-muted" aria-hidden="true" />{/if}
					</button>
				{/each}
			</div>
		</section>
	{/if}
{/snippet}

<div class="grid gap-4 md:hidden">
	<InstallPush hideWhenOn />
	{#if notifications.items.length}
		<div class="flex flex-wrap justify-end gap-2">
			<button type="button" class="btn btn-outline btn-sm min-h-10 gap-1.5" disabled={notifications.unread === 0} onclick={() => notifications.markAllRead()}><CheckCheck size={14} aria-hidden="true" />{t('portal.mark_all_read')}</button>
			{#if confirmClear}
				<button type="button" class="btn btn-sm min-h-10 gap-1.5 border-danger bg-danger text-white" onclick={clear}><Trash2 size={14} aria-hidden="true" />{t('portal.delete_all_confirm')}</button>
			{:else}
				<button type="button" class="btn btn-outline btn-sm min-h-10 gap-1.5" onclick={() => (confirmClear = true)}><Trash2 size={14} aria-hidden="true" />{t('portal.delete_all')}</button>
			{/if}
		</div>
		{@render group(t('portal.inbox_today'), today)}
		{@render group(t('portal.inbox_earlier'), earlier)}
	{:else}
		<div class="card card-body items-center gap-2 py-8 text-center text-muted"><Bell size={24} aria-hidden="true" /><p class="text-sm">{t('portal.inbox_empty')}</p></div>
	{/if}
</div>
