<script lang="ts">
	import { goto } from '$app/navigation';
	import { Lock } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ColorField from '#lib/components/ColorField.svelte';
	import PlayersEditor from '#lib/components/tournaments/PlayersEditor.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { blankPlayer, isBlank, playerBody, type PlayerDraft } from '#lib/tournaments-public.ts';

	let { data } = $props();
	const tn = $derived(data.tournament);

	let name = $state('');
	let contactName = $state('');
	let contactEmail = $state('');
	let contactPhone = $state('');
	let primary = $state('');
	let secondary = $state('');
	let logo = $state<File | null>(null);
	let website = $state('');
	// svelte-ignore state_referenced_locally
	let players = $state<PlayerDraft[]>([blankPlayer('player', data.tournament.lunch_mode === 'mandatory' ? [...data.tournament.dates] : [])]);
	let errors = $state<Record<string, string>>({});
	let formError = $state('');
	let busy = $state(false);
	let closedNow = $state(false);

	const closed = $derived(!tn.accepts_registrations || closedNow);
	const summary = $derived(Object.entries(errors).filter(([, m]) => !!m));
	const fieldMsg = (key: string) => errors[key] ?? '';

	function clientErrors(): Record<string, string> {
		const out: Record<string, string> = {};
		const need = t('reg.err.required');
		if (!name.trim()) out.foreign_name = need;
		if (!contactName.trim()) out.foreign_contact_name = need;
		if (!contactEmail.trim()) out.foreign_contact_email = need;
		players.forEach((p, i) => {
			if (isBlank(p)) return;
			if (!p.first_name.trim()) out[`players.${i}.first_name`] = need;
			if (!p.last_name.trim()) out[`players.${i}.last_name`] = need;
		});
		if (!players.some((p) => !isBlank(p))) out.players = t('tp.err.no_players');
		return out;
	}

	function onLogo(e: Event & { currentTarget: HTMLInputElement }) {
		const file = e.currentTarget.files?.[0] ?? null;
		if (file && file.size > 2 * 1024 * 1024) {
			errors = { ...errors, logo: t('tp.err.logo_size') };
			e.currentTarget.value = '';
			logo = null;
			return;
		}
		errors = { ...errors, logo: '' };
		logo = file;
	}

	async function submit(ev: SubmitEvent) {
		ev.preventDefault();
		if (busy) return;
		formError = '';
		const local = clientErrors();
		errors = local;
		if (Object.keys(local).length) {
			queueMicrotask(() => document.getElementById('tp-errors')?.focus());
			return;
		}
		busy = true;
		const res = await api<{ status_token: string }>('POST', `/public/tournaments/${encodeURIComponent(tn.slug)}/registrations`, {
			body: {
				foreign_name: name.trim(),
				foreign_contact_name: contactName.trim(),
				foreign_contact_email: contactEmail.trim(),
				foreign_contact_phone: contactPhone.trim(),
				foreign_primary_color: /^#[0-9a-f]{6}$/i.test(primary) ? primary : '',
				foreign_secondary_color: /^#[0-9a-f]{6}$/i.test(secondary) ? secondary : '',
				players: players.map(playerBody),
				website
			}
		});
		if (res.ok && res.data) {
			const token = res.data.status_token;
			// The logo goes up as a second call; a failure there is not fatal: the status page offers the upload again.
			if (logo && !website) {
				const form = new FormData();
				form.set('logo', logo);
				await api('POST', `/public/tournaments/${encodeURIComponent(tn.slug)}/registrations/${encodeURIComponent(token)}/logo`, { form });
			}
			await goto(`/tournaments/${tn.path}/registration/${token}?submitted=1`);
			return;
		}
		busy = false;
		if (res.code === 'closed') closedNow = true;
		else if (res.status === 429 || res.code === 'rate_limited') formError = t('reg.err.rate_limited');
		else if (res.status === 0) formError = t('reg.err.network');
		else if (res.status === 404) formError = t('tp.err.not_accepting');
		else if (Object.keys(res.fields).length) {
			errors = res.fields;
			formError = t('reg.err.fix');
		} else formError = res.message || t('reg.err.generic');
		queueMicrotask(() => document.getElementById('tp-errors')?.focus());
	}
</script>

<svelte:head>
	<title>{pageTitle(`${tn.name} · ${t('tp.nav.register')}`)}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

{#if closed}
	<div class="card card-body items-center gap-2 py-10 text-center" role="status">
		<Lock size={32} class="text-dim" aria-hidden="true" />
		<p class="font-display text-xl font-bold text-ink">{t('tp.closed.title')}</p>
		<p class="text-muted">{t('tp.closed.body')}</p>
		{#if tn.contact_email}<p class="text-sm"><a class="link" href="mailto:{tn.contact_email}">{tn.contact_email}</a></p>{/if}
	</div>
{:else}
	<form class="grid gap-4" onsubmit={submit} novalidate>
		<p class="text-muted">{t('tp.register.intro')}</p>
		{#if tn.entry_fee_amount}<p class="text-sm text-muted">{t('tp.fee_line', { fee: money(tn.entry_fee_amount) })}{#if tn.deposit_amount} · {t('tp.deposit_line', { deposit: money(tn.deposit_amount) })}{/if}</p>{/if}

		<div id="tp-errors" tabindex="-1" class="outline-none" role={formError || summary.length ? 'alert' : undefined}>
			{#if formError || summary.length}
				<Alert kind="error" title={formError || t('reg.err.fix')}>
					{#if summary.length}<ul class="list-disc pl-5 text-sm">{#each summary as [key, msg] (key)}<li>{msg}</li>{/each}</ul>{/if}
				</Alert>
			{/if}
		</div>

		<section class="card card-body gap-4" aria-labelledby="tp-team">
			<h2 id="tp-team" class="font-display text-base font-bold text-ink">{t('tp.register.team')}</h2>
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="form-control sm:col-span-2">
					<label class="label" for="t-name"><span class="label-text">{t('tp.f.team_name')}<span class="text-error" aria-hidden="true"> *</span></span></label>
					<input id="t-name" class="input input-bordered w-full {fieldMsg('foreign_name') ? 'input-error' : ''}" bind:value={name} autocomplete="organization" aria-invalid={fieldMsg('foreign_name') ? 'true' : undefined} />
					{#if fieldMsg('foreign_name')}<span class="mt-1 block text-xs text-error">{fieldMsg('foreign_name')}</span>{/if}
				</div>
				<div class="form-control">
					<label class="label" for="t-contact"><span class="label-text">{t('tp.f.contact_name')}<span class="text-error" aria-hidden="true"> *</span></span></label>
					<input id="t-contact" class="input input-bordered w-full {fieldMsg('foreign_contact_name') ? 'input-error' : ''}" bind:value={contactName} autocomplete="name" aria-invalid={fieldMsg('foreign_contact_name') ? 'true' : undefined} />
					{#if fieldMsg('foreign_contact_name')}<span class="mt-1 block text-xs text-error">{fieldMsg('foreign_contact_name')}</span>{/if}
				</div>
				<div class="form-control">
					<label class="label" for="t-email"><span class="label-text">{t('reg.f.email')}<span class="text-error" aria-hidden="true"> *</span></span></label>
					<input id="t-email" type="email" class="input input-bordered w-full {fieldMsg('foreign_contact_email') ? 'input-error' : ''}" bind:value={contactEmail} autocomplete="email" aria-invalid={fieldMsg('foreign_contact_email') ? 'true' : undefined} />
					{#if fieldMsg('foreign_contact_email')}<span class="mt-1 block text-xs text-error">{fieldMsg('foreign_contact_email')}</span>{:else}<span class="label-text-alt mt-1 block">{t('tp.f.email_help')}</span>{/if}
				</div>
				<div class="form-control">
					<label class="label" for="t-phone"><span class="label-text">{t('reg.f.phone')}</span></label>
					<input id="t-phone" type="tel" class="input input-bordered w-full" bind:value={contactPhone} autocomplete="tel" />
					{#if fieldMsg('foreign_contact_phone')}<span class="mt-1 block text-xs text-error">{fieldMsg('foreign_contact_phone')}</span>{/if}
				</div>
				<div class="form-control">
					<label class="label" for="t-logo"><span class="label-text">{t('tp.f.logo')}</span></label>
					<input id="t-logo" type="file" accept="image/png,image/jpeg,image/gif,image/webp" class="file-input file-input-bordered w-full" onchange={onLogo} />
					<span class="mt-1 block text-xs {fieldMsg('logo') ? 'text-error' : 'text-muted'}">{fieldMsg('logo') || t('tp.f.logo_help')}</span>
				</div>
				<ColorField label={t('tp.f.primary_color')} bind:value={primary} error={fieldMsg('foreign_primary_color')} />
				<ColorField label={t('tp.f.secondary_color')} bind:value={secondary} error={fieldMsg('foreign_secondary_color')} />
			</div>
		</section>

		<section class="card card-body gap-3" aria-labelledby="tp-roster">
			<div>
				<h2 id="tp-roster" class="font-display text-base font-bold text-ink">{t('tp.register.roster')}</h2>
				<p class="text-sm text-muted">{t('tp.register.roster_help')}</p>
			</div>
			<PlayersEditor bind:players tournament={tn} {errors} />
		</section>

		<!-- Honeypot: real visitors never see or fill this. -->
		<div class="absolute -left-[9999px] h-0 w-0 overflow-hidden" aria-hidden="true">
			<label>Website <input type="text" name="website" tabindex="-1" autocomplete="off" bind:value={website} /></label>
		</div>

		<button type="submit" class="btn btn-primary w-full sm:w-auto sm:self-start" disabled={busy}>{busy ? t('reg.submitting') : t('tp.register.submit')}</button>
		<p class="text-xs text-muted">{t('tp.register.after')}</p>
	</form>
{/if}
