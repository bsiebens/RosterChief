<script lang="ts">
	import { Check, Copy, ExternalLink, Link as LinkIcon, RefreshCw, Trash2, Link2 } from '@lucide/svelte';
	import { invalidateAll } from '$app/navigation';
	import Alert from '#lib/components/Alert.svelte';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';

	/** Members managers: switch the personal public link to this person's game stats on, replace it or switch it off. */
	let { memberId, token }: { memberId: string; token: string | null } = $props();

	let busy = $state(false);
	let failed = $state(false);
	let copied = $state(false);
	const url = $derived(token ? `${typeof location === 'undefined' ? '' : location.origin}/stats/${token}` : '');

	async function change(method: 'POST' | 'DELETE', query?: Record<string, string>) {
		busy = true;
		failed = false;
		const res = await api(method, `/members/${memberId}/stats-share`, { query });
		busy = false;
		if (res.ok) await invalidateAll();
		else failed = true;
	}
	async function copy() {
		try {
			await navigator.clipboard.writeText(url);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			/* the field is selectable as a fallback */
		}
	}
</script>

<div class="card card-body gap-2 p-4">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h3 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase"><Link2 size={16} aria-hidden="true" /> {t('publicstats.share.title')}</h3>
		{#if !token}<button class="btn btn-outline btn-sm gap-1.5" type="button" disabled={busy} onclick={() => change('POST')}><LinkIcon size={14} /> {t('publicstats.share.create')}</button>{/if}
	</div>
	<p class="text-xs text-muted">{t('publicstats.share.hint')}</p>
	{#if failed}<Alert kind="error">{t('publicstats.share.failed')}</Alert>{/if}
	{#if token}
		<div class="flex flex-wrap items-center gap-2">
			<input class="input input-sm min-w-0 flex-1 font-mono text-xs" readonly value={url} aria-label={t('publicstats.share.link_label')} onfocus={(e) => e.currentTarget.select()} />
			<button class="btn btn-outline btn-sm gap-1.5" type="button" onclick={copy}>{#if copied}<Check size={14} /> {t('publicstats.share.copied')}{:else}<Copy size={14} /> {t('publicstats.share.copy')}{/if}</button>
			<a class="btn btn-outline btn-sm gap-1.5" href={url} target="_blank" rel="noopener"><ExternalLink size={14} /> {t('publicstats.share.open')}</a>
			<button class="btn btn-outline btn-sm gap-1.5" type="button" disabled={busy} title={t('publicstats.share.rotate_hint')} onclick={() => change('POST', { rotate: 'true' })}><RefreshCw size={14} /> {t('publicstats.share.rotate')}</button>
			<button class="btn btn-outline btn-error btn-sm gap-1.5" type="button" disabled={busy} onclick={() => change('DELETE')}><Trash2 size={14} /> {t('publicstats.share.off')}</button>
		</div>
	{/if}
</div>
