<script lang="ts">
	import { Bell, BellRing, Check } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';

	/**
	 * The alert bell of the public tournament pages: follow one or more teams and choose which push alerts to get (5 minutes before a game,
	 * start, goals, penalties, the end, and blocks that are not lunch). The browser's push subscription is the identity, no account.
	 */
	let { slug }: { slug: string } = $props();

	const KINDS = ['pre_game', 'start', 'goals', 'penalties', 'end', 'blocks'] as const;
	const base = $derived(`/public/tournaments/${encodeURIComponent(slug)}`);

	let open = $state(false);
	let supported = $state(true);
	let publicKey = $state<string | null>(null);
	let following = $state(false);
	let teams = $state<{ slug: string; name: string }[]>([]);
	let picked = $state<string[]>([]);
	let prefs = $state<Record<string, boolean>>(Object.fromEntries(KINDS.map((k) => [k, true])));
	let busy = $state(false);
	let message = $state('');
	let denied = $state(false);

	const supportedHere = () => typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

	async function registration() {
		await navigator.serviceWorker.register('/service-worker.js');
		return navigator.serviceWorker.ready;
	}

	async function load() {
		supported = supportedHere();
		const [cfg, list] = await Promise.all([api<{ public_key: string | null }>('GET', `${base}/alerts/config`), api<{ teams: { slug: string; name: string }[] }>('GET', `${base}/teams`)]);
		publicKey = cfg.data?.public_key ?? null;
		teams = list.data?.teams ?? [];
		denied = supported && Notification.permission === 'denied';
		if (!supported || !publicKey) return;
		const sub = await (await registration()).pushManager.getSubscription();
		if (!sub) return;
		const status = await api<{ subscribed: boolean; team_slugs: string[]; prefs: Record<string, boolean> }>('POST', `${base}/alerts/status`, { body: { endpoint: sub.endpoint } });
		if (status.data?.subscribed) {
			following = true;
			picked = status.data.team_slugs;
			prefs = { ...prefs, ...status.data.prefs };
		}
	}
	onMount(() => {
		load().catch(() => {});
	});

	const toKey = (b64: string) => {
		const raw = atob((b64 + '='.repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
		return Uint8Array.from(raw, (c) => c.charCodeAt(0));
	};

	async function save() {
		message = '';
		if (!picked.length) {
			message = t('ts.alerts.pick_team');
			return;
		}
		busy = true;
		try {
			if (Notification.permission !== 'granted' && (await Notification.requestPermission()) !== 'granted') {
				denied = true;
				return;
			}
			const reg = await registration();
			const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: toKey(publicKey!) }));
			const json = sub.toJSON();
			const res = await api('PUT', `${base}/alerts`, { body: { endpoint: sub.endpoint, keys: json.keys, team_slugs: picked, prefs, lang: getLang() } });
			if (!res.ok) {
				message = t('ts.alerts.failed');
				return;
			}
			following = true;
			message = t('ts.alerts.saved');
		} catch {
			message = t('ts.alerts.failed');
		} finally {
			busy = false;
		}
	}

	async function stop() {
		busy = true;
		try {
			const sub = await (await registration()).pushManager.getSubscription();
			if (sub) await api('POST', `${base}/alerts/unfollow`, { body: { endpoint: sub.endpoint } });
			following = false;
			picked = [];
			message = t('ts.alerts.stopped');
		} finally {
			busy = false;
		}
	}
	const toggle = (s: string) => (picked = picked.includes(s) ? picked.filter((x) => x !== s) : [...picked, s]);
</script>

<button type="button" class="relative flex items-center gap-2 rounded-full bg-white/15 px-3 py-1.5 font-display text-xs font-extrabold tracking-[.08em] uppercase hover:bg-white/25" onclick={() => (open = true)} aria-label={t('ts.alerts.title')}>
	{#if following}<BellRing size={15} aria-hidden="true" />{:else}<Bell size={15} aria-hidden="true" />{/if}
	<span class="hidden sm:inline">{t('ts.alerts.button')}</span>
	{#if following}<span class="absolute -top-0.5 -right-0.5 size-2.5 rounded-full border-2 border-primary bg-emerald-400"></span>{/if}
</button>

<Modal bind:open title={t('ts.alerts.title')}>
	<div class="grid gap-4 text-ink">
		{#if !supported}
			<p class="text-sm text-muted">{t('ts.alerts.unsupported')}</p>
		{:else if !publicKey}
			<p class="text-sm text-muted">{t('ts.alerts.unavailable')}</p>
		{:else}
			<p class="text-sm text-muted">{t('ts.alerts.intro')}</p>
			{#if denied}<p class="rounded-lg border border-danger/40 bg-danger/5 p-3 text-sm text-danger" role="alert">{t('ts.alerts.denied')}</p>{/if}
			<fieldset class="grid gap-1.5">
				<legend class="mb-1 font-display text-xs font-extrabold tracking-[.1em] text-muted uppercase">{t('ts.alerts.teams')}</legend>
				<div class="grid max-h-48 gap-1 overflow-y-auto sm:grid-cols-2">
					{#each teams as tm (tm.slug)}
						<label class="flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-sm {picked.includes(tm.slug) ? 'border-club bg-club/5' : ''}"><input type="checkbox" class="checkbox checkbox-sm" checked={picked.includes(tm.slug)} onchange={() => toggle(tm.slug)} />{tm.name}</label>
					{/each}
				</div>
			</fieldset>
			<fieldset class="grid gap-1.5">
				<legend class="mb-1 font-display text-xs font-extrabold tracking-[.1em] text-muted uppercase">{t('ts.alerts.what')}</legend>
				<div class="grid gap-1 sm:grid-cols-2">
					{#each KINDS as k (k)}
						<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={prefs[k]} />{t(`ts.alerts.k.${k}`)}</label>
					{/each}
				</div>
			</fieldset>
			{#if message}<p class="flex items-center gap-1.5 text-sm text-ink" role="status"><Check size={14} aria-hidden="true" />{message}</p>{/if}
			<div class="flex flex-wrap justify-end gap-2">
				{#if following}<button type="button" class="btn btn-outline" disabled={busy} onclick={stop}>{t('ts.alerts.stop')}</button>{/if}
				<button type="button" class="btn btn-primary" disabled={busy} onclick={save}>{following ? t('ts.alerts.update') : t('ts.alerts.enable')}</button>
			</div>
			<p class="text-xs text-muted">{t('ts.alerts.ios')}</p>
		{/if}
	</div>
</Modal>
