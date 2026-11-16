<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { BellRing, Check, Mail, Moon, Smartphone } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import InstallPush from '#lib/components/InstallPush.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { NotificationSettings } from '#lib/portal.ts';
	import SaveStatus from './SaveStatus.svelte';

	/**
	 * What the person hears about and how. There is no Save button anywhere on this screen: every switch, choice and time saves the moment it
	 * changes, and the status line on top says so and confirms each save.
	 */
	let { settings }: { settings: NotificationSettings } = $props();

	let status = $state<'idle' | 'saving' | 'saved' | 'error'>('idle');
	let error = $state('');
	let timer: ReturnType<typeof setTimeout> | undefined;
	// svelte-ignore state_referenced_locally
	let quiet = $state({ enabled: false, start: '22:00', end: '07:00', timezone: 'Europe/Brussels', ...(settings.quiet_hours ?? {}) });
	$effect(() => {
		if (settings.quiet_hours) quiet = { ...settings.quiet_hours };
	});

	async function send(body: object) {
		clearTimeout(timer);
		status = 'saving';
		error = '';
		const result = await api<NotificationSettings>('PUT', '/portal/me/notifications', { body });
		if (result.ok) {
			status = 'saved';
			await invalidateAll();
			timer = setTimeout(() => (status = 'idle'), 2500);
		} else {
			status = 'error';
			error = result.message ?? t('common.error.generic');
		}
	}
	const toggleCategory = (key: string, channel: 'push' | 'email', value: boolean) => send({ categories: { [key]: { [channel]: value } } });
	const saveQuiet = () => {
		if (quiet.start && quiet.end && quiet.start !== quiet.end) void send({ quiet_hours: quiet });
	};
	const channels = [
		{ key: 'push', label: 'portal.notif_push', icon: Smartphone },
		{ key: 'email', label: 'portal.notif_email', icon: Mail }
	] as const;
</script>

<SaveStatus {status} />
<div class="grid min-w-0 items-start gap-4 xl:grid-cols-2">
	<div class="grid min-w-0 gap-4">
	{#if error}<Alert kind="error">{error}</Alert>{/if}

	<section class="card overflow-hidden" aria-labelledby="notif-title">
		<div class="card-h"><BellRing size={17} aria-hidden="true" /><h2 id="notif-title">{t('portal.notif_what')}</h2></div>
		<div class="grid grid-cols-[1fr_5rem_5rem] items-center gap-x-4 border-b border-line bg-subhead px-5 py-2 font-mono text-[11px] tracking-[.1em] text-muted uppercase">
			<span>{t('portal.notif_category')}</span>
			{#each channels as c (c.key)}<span class="flex items-center justify-center gap-1.5"><c.icon size={13} aria-hidden="true" />{t(c.label)}</span>{/each}
		</div>
		<div class="grid grid-cols-[1fr_5rem_5rem] items-center gap-x-4 border-b border-line px-5 py-3">
			<span><b class="block text-ink">{t('portal.notif_all')}</b><span class="text-xs text-muted">{t('portal.notif_all_help')}</span></span>
			{#each channels as c (c.key)}
				<span class="flex justify-center"><input type="checkbox" class="toggle" aria-label="{t('portal.notif_all')} · {t(c.label)}" checked={settings.master[c.key]} onchange={(e) => send({ master: { [c.key]: e.currentTarget.checked } })} /></span>
			{/each}
		</div>
		{#each settings.categories as category (category.key)}
			<div class="grid grid-cols-[1fr_5rem_5rem] items-center gap-x-4 border-b border-line px-5 py-2.5 last:border-b-0 {settings.master.push || settings.master.email ? '' : 'opacity-60'}">
				<span class="text-sm text-ink">{category.label}</span>
				{#each channels as c (c.key)}
					<span class="flex justify-center"><input type="checkbox" class="toggle toggle-sm" aria-label="{category.label} · {t(c.label)}" checked={category[c.key]} disabled={!settings.master[c.key]} onchange={(e) => toggleCategory(category.key, c.key, e.currentTarget.checked)} /></span>
				{/each}
			</div>
		{/each}
	</section>
	</div>

	<div class="grid min-w-0 gap-4">
	<section class="card" aria-labelledby="email-title">
		<div class="card-h"><Mail size={17} aria-hidden="true" /><h2 id="email-title">{t('portal.notif_email_when')}</h2></div>
		<div class="grid gap-3 p-5">
			<label class="flex items-start gap-3 text-sm"><input type="radio" class="radio mt-0.5" name="email-mode" checked={settings.email_mode === 'always'} onchange={() => send({ email_mode: 'always' })} /><span><b class="block text-ink">{t('portal.notif_email_always')}</b></span></label>
			<label class="flex items-start gap-3 text-sm"><input type="radio" class="radio mt-0.5" name="email-mode" checked={settings.email_mode === 'unless_app'} onchange={() => send({ email_mode: 'unless_app' })} /><span><b class="block text-ink">{t('portal.notif_email_unless_app')}</b></span></label>
		</div>
	</section>

	<section class="card" aria-labelledby="quiet-title">
		<div class="card-h"><Moon size={17} aria-hidden="true" /><h2 id="quiet-title">{t('portal.notif_quiet')}</h2><input type="checkbox" class="toggle ml-auto" aria-label={t('portal.notif_quiet_on')} bind:checked={quiet.enabled} onchange={saveQuiet} /></div>
		<div class="grid gap-3 p-5">
			<p class="text-sm text-muted">{t('portal.notif_quiet_help')}</p>
			<div class="grid grid-cols-2 gap-3 sm:max-w-sm {quiet.enabled ? '' : 'opacity-50'}">
				<label class="grid gap-1 text-sm font-semibold">{t('portal.notif_quiet_from')}<input class="input font-normal" type="time" bind:value={quiet.start} disabled={!quiet.enabled} onchange={saveQuiet} required /></label>
				<label class="grid gap-1 text-sm font-semibold">{t('portal.notif_quiet_to')}<input class="input font-normal" type="time" bind:value={quiet.end} disabled={!quiet.enabled} onchange={saveQuiet} required /></label>
			</div>
			<p class="text-xs text-muted">{quiet.timezone}</p>
		</div>
	</section>

	<InstallPush />
	</div>
</div>
