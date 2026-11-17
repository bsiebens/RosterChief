<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { ChartColumn, Check, Copy, Link2, RefreshCw } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { StatsPerson } from '#lib/portal.ts';

	/**
	 * Personal statistics for one person: a link to their statistics page and the switch for the public link. The link carries a secret;
	 * switching it off or replacing it makes the old link stop working at once.
	 */
	let { person, banner = false }: { person: StatsPerson; banner?: boolean } = $props();
	let busy = $state(false);
	let copied = $state(false);
	let failed = $state(false);
	const url = $derived(person.share_token ? `${location.origin}/stats/${person.share_token}` : '');

	async function set(on: boolean, rotate = false) {
		busy = true;
		failed = false;
		const result = on ? await api('PUT', `/portal/me/stats/${person.id}/share`, { query: rotate ? { rotate: 'true' } : {} }) : await api('DELETE', `/portal/me/stats/${person.id}/share`);
		busy = false;
		if (!result.ok) failed = true;
		else await invalidateAll();
	}
	async function copy() {
		try {
			await navigator.clipboard.writeText(url);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			failed = true;
		}
	}
</script>

{#if banner}
	<section class="card min-w-0 max-w-full" aria-labelledby="stats-share-title">
		<div class="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3 sm:px-5">
			<div class="flex min-w-0 flex-1 basis-60 items-center gap-3">
				<Link2 size={18} class="shrink-0 text-muted" aria-hidden="true" />
				<div class="min-w-0"><b id="stats-share-title" class="block text-ink">{t('portal.stats_public')}</b><span class="block text-xs text-muted">{t('portal.stats_public_help', { name: person.first_name })}</span></div>
			</div>
			<input type="checkbox" class="toggle order-2 shrink-0 sm:order-3" aria-labelledby="stats-share-title" checked={!!person.share_token} disabled={busy} onchange={(e) => set(e.currentTarget.checked)} />
			{#if person.share_token}
				<div class="order-last flex w-full min-w-0 flex-wrap items-center gap-2 sm:order-2 sm:w-auto sm:flex-1 sm:flex-nowrap">
					<div class="input flex min-w-0 flex-1 basis-48 items-center gap-2 bg-subhead text-sm"><span class="truncate" title={url}>{url}</span></div>
					<button class="btn btn-outline btn-sm gap-1.5" type="button" onclick={copy}>{#if copied}<Check size={14} aria-hidden="true" />{t('portal.stats_copied')}{:else}<Copy size={14} aria-hidden="true" />{t('portal.stats_copy')}{/if}</button>
					<button class="btn btn-outline btn-sm gap-1.5" type="button" disabled={busy} onclick={() => set(true, true)}><RefreshCw size={13} aria-hidden="true" />{t('portal.stats_replace_short')}</button>
				</div>
			{/if}
		</div>
		{#if failed}<div class="px-5 pb-3"><Alert kind="error">{t('common.error.generic')}</Alert></div>{/if}
	</section>
{:else}
<section class="card" aria-labelledby="stats-share-title">
	<div class="card-h"><ChartColumn size={17} aria-hidden="true" /><h2 id="stats-share-title">{t('portal.stats_title')}</h2><a class="link-more ml-auto" href="/parent/stats?who={person.id}">{t('portal.stats_open')} →</a></div>
	<div class="grid gap-3 p-5">
		<label class="flex items-center gap-3">
			<input type="checkbox" class="toggle" checked={!!person.share_token} disabled={busy} onchange={(e) => set(e.currentTarget.checked)} />
			<span class="min-w-0"><b class="block text-ink">{t('portal.stats_public')}</b><span class="block text-xs text-muted">{t('portal.stats_public_help', { name: person.first_name })}</span></span>
		</label>
		{#if person.share_token}
			<div class="flex items-center gap-2">
				<div class="input flex min-w-0 flex-1 items-center gap-2 bg-subhead text-sm"><Link2 size={14} class="shrink-0 text-muted" aria-hidden="true" /><span class="truncate" title={url}>{url}</span></div>
				<button class="btn btn-outline btn-sm gap-1.5" type="button" onclick={copy}>{#if copied}<Check size={14} aria-hidden="true" />{t('portal.stats_copied')}{:else}<Copy size={14} aria-hidden="true" />{t('portal.stats_copy')}{/if}</button>
			</div>
			<button class="link inline-flex w-fit items-center gap-1.5 text-xs font-semibold" type="button" disabled={busy} onclick={() => set(true, true)}><RefreshCw size={12} aria-hidden="true" />{t('portal.stats_replace')}</button>
			<p class="text-xs text-muted">{t('portal.stats_note')}</p>
		{/if}
		{#if failed}<Alert kind="error">{t('common.error.generic')}</Alert>{/if}
	</div>
</section>
{/if}
