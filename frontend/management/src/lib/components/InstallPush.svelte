<script lang="ts">
	import { Bell, Download, Smartphone } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pwa, registerPwa } from '#lib/pwa.svelte.ts';

	let browserReady = $state(false);
	let supported = $state(false);
	let ios = $state(false);
	let busy = $state(false);
	let subscribed = $state(false);
	let denied = $state(false);
	let pushConfigLoaded = $state(false);
	let key = $state<string | null>(null);
	let message = $state('');

	const decodeKey = (value: string) => Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
	async function getRegistration() {
		const registration = await registerPwa();
		return registration.active ? registration : navigator.serviceWorker.ready;
	}

	onMount(() => {
		ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
		supported = window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
		browserReady = true;
		if (!supported) return;
		denied = Notification.permission === 'denied';
		void loadStatus();
	});

	async function loadStatus() {
		const config = await api<{ public_key: string | null }>('GET', '/web-push/config');
		if (!config.ok) {
			message = t('pwa.push_status_error');
			return;
		}
		key = config.data?.public_key ?? null;
		pushConfigLoaded = true;
		if (!key) return;
		try {
			const registration = await getRegistration();
			const subscription = await registration.pushManager.getSubscription();
			if (subscription) {
				const status = await api<{ subscribed: boolean }>('POST', '/web-push/status', { body: { endpoint: subscription.endpoint } });
				subscribed = status.data?.subscribed === true;
			}
		} catch {
			message = t('pwa.push_error');
		}
	}

	async function install() {
		const prompt = pwa.prompt;
		if (!prompt) return;
		try {
			await prompt.prompt();
			await prompt.userChoice;
		} finally {
			pwa.prompt = null;
		}
	}

	async function togglePush() {
		if (!key || busy) return;
		const publicKey = key;
		busy = true;
		message = '';
		try {
			if (!subscribed) {
				const permission = await Notification.requestPermission();
				denied = permission === 'denied';
				if (permission !== 'granted') return;
			}
			const registration = await getRegistration();
			let subscription = await registration.pushManager.getSubscription();
			if (subscribed) {
				if (subscription) {
					const result = await api('DELETE', '/web-push/subscriptions', { body: { endpoint: subscription.endpoint } });
					if (!result.ok) throw new Error('unsubscribe');
					await subscription.unsubscribe();
				}
				subscribed = false;
			} else {
				subscription ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decodeKey(publicKey) });
				const result = await api('POST', '/web-push/subscriptions', { body: subscription.toJSON() });
				if (!result.ok) {
					message = t(result.status === 409 ? 'pwa.push_other_account' : 'pwa.push_error');
					return;
				}
				subscribed = true;
			}
		} catch {
			message = t('pwa.push_error');
		} finally {
			busy = false;
		}
	}
</script>

<section class="card card-body gap-3">
	<h2 class="card-title"><Smartphone size={16} aria-hidden="true" />{t('pwa.title')}</h2>
	{#if browserReady && !pwa.installed}
		<div class="grid gap-2">
			{#if pwa.prompt}
				<button type="button" class="btn btn-outline w-fit gap-2" onclick={install}><Download size={16} aria-hidden="true" />{t('pwa.install')}</button>
			{:else}
				<p class="text-sm text-muted">{t(ios ? 'pwa.ios_install' : 'pwa.menu_install')}</p>
			{/if}
		</div>
	{/if}
	{#if supported && key && !denied}
		<button type="button" class="btn {subscribed ? 'btn-outline' : 'btn-primary'} w-fit gap-2" disabled={busy} onclick={togglePush}><Bell size={16} aria-hidden="true" />{t(subscribed ? 'pwa.disable' : 'pwa.enable')}</button>
	{:else if supported && pushConfigLoaded && !key && !denied}
		<p class="text-sm text-muted">{t('pwa.push_server_unavailable')}</p>
	{:else if browserReady && (denied || !supported)}
		<p class="text-sm text-muted">{t(denied ? 'pwa.push_denied' : ios && !pwa.installed ? 'pwa.ios_push' : 'pwa.push_unavailable')}</p>
	{/if}
	{#if message}<p class="text-sm text-danger" role="alert">{message}</p>{/if}
</section>
