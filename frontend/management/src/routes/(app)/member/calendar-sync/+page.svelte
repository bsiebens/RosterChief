<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { Check, Copy, RefreshCw } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	/**
	 * Sync to my calendar as a settings page: a switch on top for the secret calendar link (own events plus the children's), and, while it is on,
	 * the link, the link for Apple Calendar and Outlook, a hint per calendar app and a way to replace the link (the old one stops working).
	 */
	usePageHeader(() => ({ title: t('portal.cal_title') }));
	let token = $state<string | null>(null);
	let loaded = $state(false);
	let busy = $state(false);
	let failed = $state(false);
	let copied = $state('');
	const https = $derived(token ? `${page.url.origin}/api/v1/public/calendar/${token}.ics` : '');
	const webcal = $derived(https.replace(/^https?:/, 'webcal:'));

	onMount(async () => {
		const res = await api<{ enabled: boolean; url_token: string | null }>('GET', '/portal/me/calendar-feed');
		if (res.ok) token = res.data?.url_token ?? null;
		else failed = true;
		loaded = true;
	});
	async function change(on: boolean, rotate = false) {
		busy = true;
		failed = false;
		const res = on ? await api<{ url_token: string | null }>('PUT', '/portal/me/calendar-feed', { query: rotate ? { rotate: 'true' } : {} }) : await api<{ url_token: string | null }>('DELETE', '/portal/me/calendar-feed');
		busy = false;
		if (res.ok) token = res.data?.url_token ?? null;
		else failed = true;
	}
	async function copy(what: string, value: string) {
		try {
			await navigator.clipboard.writeText(value);
			copied = what;
			setTimeout(() => (copied = ''), 2000);
		} catch {
			failed = true;
		}
	}
</script>

<svelte:head><title>{pageTitle(t('portal.cal_title'))}</title></svelte:head>

<div class="grid max-w-2xl min-w-0 grid-cols-[minmax(0,1fr)] gap-5">
	<label class="flex min-h-16 items-center gap-3 rounded-box border border-line bg-(--portal-panel) px-4 py-3">
		<span class="min-w-0 flex-1"><b class="block text-ink">{t('portal.sync_calendar')}</b><span class="block text-sm text-muted">{t('portal.cal_intro')}</span></span>
		<input type="checkbox" class="toggle shrink-0" checked={!!token} disabled={busy || !loaded} aria-label={t('portal.sync_calendar')} onchange={(e) => change(e.currentTarget.checked)} />
	</label>

	{#if failed}<Alert kind="error">{t('common.error.generic')}</Alert>{/if}

	{#if loaded && token}
		{#each [{ key: 'https', label: t('portal.cal_link'), value: https }, { key: 'webcal', label: t('portal.cal_webcal'), value: webcal }] as row (row.key)}
			<div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-1.5">
				<span class="text-sm font-semibold text-muted">{row.label}</span>
				<div class="input flex min-w-0 items-center bg-subhead text-sm"><span class="truncate" title={row.value}>{row.value}</span></div>
				<button class="btn btn-outline w-full gap-1.5" type="button" onclick={() => copy(row.key, row.value)}>
					{#if copied === row.key}<Check size={14} aria-hidden="true" />{t('portal.cal_copied')}{:else}<Copy size={14} aria-hidden="true" />{t('portal.cal_copy')}{/if}
				</button>
			</div>
		{/each}
		<a class="link w-fit text-sm font-semibold" href={webcal}>{t('portal.cal_open')}</a>
		<ul class="grid gap-2 text-sm text-muted">
			<li>{t('portal.cal_apple')}</li>
			<li>{t('portal.cal_google')}</li>
			<li>{t('portal.cal_outlook')}</li>
		</ul>
		<p class="text-xs text-muted">{t('portal.cal_secret')}</p>
		<div class="grid gap-2 border-t border-line pt-4">
			<button class="btn btn-outline w-full gap-1.5" type="button" disabled={busy} onclick={() => change(true, true)}><RefreshCw size={13} aria-hidden="true" />{t('portal.cal_replace')}</button>
			<span class="text-xs text-muted">{t('portal.cal_replace_help')}</span>
		</div>
	{/if}
</div>
