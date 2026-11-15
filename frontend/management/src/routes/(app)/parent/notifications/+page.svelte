<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { BellRing, Check, Mail, Moon, Smartphone } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import InstallPush from '#lib/components/InstallPush.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { NotificationSettings } from '#lib/portal.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: t('portal.notifications') }));

	let busy = $state(false);
	let error = $state('');
	let quiet = $state({ enabled: false, start: '22:00', end: '07:00', timezone: 'Europe/Brussels' });
	$effect(() => {
		if (data.settings.quiet_hours) quiet = { ...data.settings.quiet_hours };
	});

	async function send(body: Partial<{ master: Record<string, boolean>; categories: Record<string, Record<string, boolean>>; email_mode: string; quiet_hours: object }>) {
		busy = true;
		error = '';
		const result = await api<NotificationSettings>('PUT', '/portal/me/notifications', { body });
		busy = false;
		if (result.ok) await invalidateAll();
		else error = result.message ?? t('common.error.generic');
	}
	const toggleCategory = (key: string, channel: 'push' | 'email', value: boolean) => send({ categories: { [key]: { [channel]: value } } });
	const channels = [
		{ key: 'push', label: 'portal.notif_push', icon: Smartphone },
		{ key: 'email', label: 'portal.notif_email', icon: Mail }
	] as const;
</script>

<svelte:head><title>{pageTitle(t('portal.notifications'))}</title></svelte:head>

{#if error}<Alert kind="error">{error}</Alert>{/if}

<div class="grid min-w-0 items-start gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
	<section class="card overflow-hidden">
		<h2 class="card-title border-b border-line px-5 py-3"><BellRing size={16} aria-hidden="true" />{t('portal.notif_what')}</h2>
		<div class="grid grid-cols-[1fr_auto_auto] items-center gap-x-8 px-5">
			<div class="col-span-3 grid grid-cols-subgrid border-b border-line py-3">
				<span class="text-sm font-semibold text-ink">{t('portal.notif_all')}</span>
				{#each channels as c (c.key)}
					<label class="flex items-center justify-center gap-2 text-sm"><c.icon size={15} class="text-muted" aria-hidden="true" />{t(c.label)}
						<input type="checkbox" class="toggle" checked={data.settings.master[c.key]} disabled={busy} onchange={(e) => send({ master: { [c.key]: e.currentTarget.checked } })} />
					</label>
				{/each}
			</div>
			{#each data.settings.categories as category (category.key)}
				<div class="col-span-3 grid grid-cols-subgrid items-center border-b border-line py-2.5 last:border-b-0">
					<span class="text-sm text-ink">{category.label}</span>
					{#each channels as c (c.key)}
						<span class="flex justify-center"><input type="checkbox" class="checkbox" aria-label="{category.label} · {t(c.label)}" checked={category[c.key]} disabled={busy || !data.settings.master[c.key]} onchange={(e) => toggleCategory(category.key, c.key, e.currentTarget.checked)} /></span>
					{/each}
				</div>
			{/each}
		</div>
	</section>

	<div class="grid gap-4">
		<section class="card card-body gap-3">
			<h2 class="card-title"><Mail size={16} aria-hidden="true" />{t('portal.notif_email_when')}</h2>
			<label class="flex items-start gap-2 text-sm"><input type="radio" class="radio mt-0.5" name="email-mode" checked={data.settings.email_mode === 'always'} disabled={busy} onchange={() => send({ email_mode: 'always' })} />{t('portal.notif_email_always')}</label>
			<label class="flex items-start gap-2 text-sm"><input type="radio" class="radio mt-0.5" name="email-mode" checked={data.settings.email_mode === 'unless_app'} disabled={busy} onchange={() => send({ email_mode: 'unless_app' })} />{t('portal.notif_email_unless_app')}</label>
		</section>

		<section class="card card-body gap-3">
			<h2 class="card-title"><Moon size={16} aria-hidden="true" />{t('portal.notif_quiet')}</h2>
			<p class="text-sm text-muted">{t('portal.notif_quiet_help')}</p>
			<form class="grid gap-3" onsubmit={(e) => { e.preventDefault(); void send({ quiet_hours: quiet }); }}>
				<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="toggle" bind:checked={quiet.enabled} />{t('portal.notif_quiet_on')}</label>
				<div class="grid grid-cols-2 gap-3">
					<label class="grid gap-1 text-sm">{t('portal.notif_quiet_from')}<input class="input" type="time" bind:value={quiet.start} required /></label>
					<label class="grid gap-1 text-sm">{t('portal.notif_quiet_to')}<input class="input" type="time" bind:value={quiet.end} required /></label>
				</div>
				<button class="btn btn-primary w-fit gap-2" type="submit" disabled={busy}><Check size={16} aria-hidden="true" />{t('common.save')}</button>
			</form>
		</section>
		<InstallPush />
	</div>
</div>
