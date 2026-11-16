<script lang="ts">
	import { CalendarDays, Check, Copy, RefreshCw } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Sheet from '#lib/components/portal/Sheet.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';

	/**
	 * "Sync to my calendar": one secret calendar link per account (own events plus the children's). The button opens a small sheet with the
	 * link (https and webcal://), a hint per calendar app, and the switches to replace the link (the old one stops working) or switch it off.
	 */
	let { class: klass = 'btn btn-outline gap-1.5' }: { class?: string } = $props();
	let open = $state(false);
	let token = $state<string | null>(null);
	let loaded = $state(false);
	let busy = $state(false);
	let failed = $state(false);
	let copied = $state('');
	const https = $derived(token ? `${location.origin}/api/v1/public/calendar/${token}.ics` : '');
	const webcal = $derived(https.replace(/^https?:/, 'webcal:'));

	async function show() {
		open = true;
		failed = false;
		const res = await api<{ enabled: boolean; url_token: string | null }>('GET', '/portal/me/calendar-feed');
		if (res.ok) token = res.data?.url_token ?? null;
		else failed = true;
		loaded = true;
	}
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

<button class={klass} type="button" onclick={show}><CalendarDays size={14} aria-hidden="true" />{t('portal.sync_calendar')}</button>

<Sheet {open} title={t('portal.cal_title')} onclose={() => (open = false)}>
	<div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 px-5 pb-6">
		<h2 class="font-display text-xl font-extrabold text-ink">{t('portal.cal_title')}</h2>
		<p class="text-sm text-muted">{t('portal.cal_intro')}</p>
		{#if failed}<Alert kind="error">{t('common.error.generic')}</Alert>{/if}
		{#if !loaded}
			<p class="text-sm text-muted" aria-live="polite">…</p>
		{:else if !token}
			<p class="text-sm text-ink">{t('portal.cal_off_state')}</p>
			<button class="btn w-fit gap-1.5" type="button" disabled={busy} onclick={() => change(true)}><CalendarDays size={14} aria-hidden="true" />{t('portal.cal_enable')}</button>
		{:else}
			{#each [{ key: 'https', label: t('portal.cal_link'), value: https }, { key: 'webcal', label: t('portal.cal_webcal'), value: webcal }] as row (row.key)}
				<div class="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-1">
					<span class="text-xs font-semibold text-muted">{row.label}</span>
					<div class="flex min-w-0 items-center gap-2">
						<div class="input flex min-w-0 flex-1 items-center bg-subhead text-sm"><span class="truncate" title={row.value}>{row.value}</span></div>
						<button class="btn btn-outline btn-sm shrink-0 gap-1.5" type="button" onclick={() => copy(row.key, row.value)}>
							{#if copied === row.key}<Check size={14} aria-hidden="true" />{t('portal.cal_copied')}{:else}<Copy size={14} aria-hidden="true" />{t('portal.cal_copy')}{/if}
						</button>
					</div>
				</div>
			{/each}
			<a class="link w-fit text-sm font-semibold" href={webcal}>{t('portal.cal_open')}</a>
			<ul class="grid gap-1 text-sm text-muted">
				<li>{t('portal.cal_apple')}</li>
				<li>{t('portal.cal_google')}</li>
				<li>{t('portal.cal_outlook')}</li>
			</ul>
			<p class="text-xs text-muted">{t('portal.cal_secret')}</p>
			<div class="flex flex-wrap items-center gap-2 border-t border-line pt-4">
				<button class="btn btn-outline btn-sm gap-1.5" type="button" disabled={busy} onclick={() => change(true, true)}><RefreshCw size={13} aria-hidden="true" />{t('portal.cal_replace')}</button>
				<button class="btn btn-outline btn-sm" type="button" disabled={busy} onclick={() => change(false)}>{t('portal.cal_switch_off')}</button>
				<span class="basis-full text-xs text-muted">{t('portal.cal_replace_help')}</span>
			</div>
		{/if}
	</div>
</Sheet>
