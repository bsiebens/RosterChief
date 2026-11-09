<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { beforeNavigate } from '$app/navigation';
	import { KeyRound, Save } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import ShopNumbering from '#lib/components/shop/ShopNumbering.svelte';
	import type { NumberingChanges } from '#lib/shop-orders.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const s = $derived(data.settings);
	const formId = $props.id();

	// svelte-ignore state_referenced_locally
	let shopOpen = $state(data.settings.shop_open);
	// svelte-ignore state_referenced_locally
	let closedMessage = $state(data.settings.closed_message);
	// svelte-ignore state_referenced_locally
	let provider = $state(data.settings.payment_provider);
	// svelte-ignore state_referenced_locally
	let dueDays = $state(String(data.settings.invoice_due_days));
	// svelte-ignore state_referenced_locally
	let pickupText = $state(data.settings.pickup_instructions);
	let numberingChanges = $state<NumberingChanges>({});
	let creds = $state<Record<string, string>>({});
	let clear = $state(false);
	// The stored settings came back after a save: start from them again (secrets are never returned, so the inputs empty).
	$effect(() => {
		const stored = data.settings;
		untrack(() => {
			shopOpen = stored.shop_open;
			closedMessage = stored.closed_message;
			provider = stored.payment_provider;
			dueDays = String(stored.invoice_due_days);
			pickupText = stored.pickup_instructions;
			creds = {};
			clear = false;
		});
	});

	const info = $derived(s.providers.find((p) => p.key === provider) ?? null);
	const entered = $derived(Object.fromEntries(Object.entries(creds).filter(([, v]) => v.trim())));
	const dirty = $derived(Object.keys(numberingChanges).length > 0 || shopOpen !== s.shop_open || closedMessage.trim() !== s.closed_message || provider !== s.payment_provider || Number(dueDays) !== s.invoice_due_days || pickupText.trim() !== s.pickup_instructions || Object.keys(entered).length > 0 || clear);
	const payload = $derived(
		JSON.stringify({
			shop_open: shopOpen,
			closed_message: closedMessage.trim(),
			payment_provider: provider,
			invoice_due_days: Number(dueDays) || 0,
			pickup_instructions: pickupText.trim(),
			...(Object.keys(numberingChanges).length ? { numbering: numberingChanges } : {}),
			// omit = keep, {} = clear, otherwise replace with what was typed
			...(clear ? { credentials: {} } : Object.keys(entered).length ? { credentials: entered } : {})
		})
	);

	let busy = $state(false);
	let leaving = $state(false);
	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('shop.unsaved_confirm'))) cancel();
	});

	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const done = $derived(form && 'done' in form);
	const errorText = $derived(form && 'status' in form && form.status ? String(form.message || t('shop.err.failed')) : '');
	const webhook = (key: string) => `${page.url.origin}/api/v1/public/shop/payments/webhook/${key}`;

	usePageHeader(() => ({ title: t('shop.set.title'), context: t('shop.set.context') }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />
<svelte:head><title>{pageTitle(t('shop.set.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if done && !dirty}<Alert kind="success">{t('shop.saved')}</Alert>{/if}
	{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<form
			id={formId}
			method="POST"
			action="?/save"
			class="grid content-start gap-5 xl:col-span-2"
			use:enhance={() => {
				busy = true;
				leaving = true;
				return async ({ update }) => {
					await update({ reset: false });
					busy = false;
					leaving = false;
				};
			}}>
			<input type="hidden" name="payload" value={payload} />
			<section class="card card-body gap-3">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.set.card.availability')}</h2>
					<span class="badge {s.shop_open ? 'badge-success' : 'badge-warning'}">{s.shop_open ? t('shop.set.status_open') : t('shop.set.status_closed')}</span>
				</div>
				<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
					<input type="checkbox" class="checkbox mt-0.5" bind:checked={shopOpen} />
					<span class="grid gap-0.5">
						<span class="text-sm font-semibold text-ink">{t('shop.set.open_label')}</span>
						<span class="text-xs text-muted">{t('shop.set.open_help')}</span>
					</span>
				</label>
				<div class="form-control w-full">
					<label class="label" for="{formId}-closed"><span class="label-text">{t('shop.set.closed_message')}</span></label>
					<textarea id="{formId}-closed" class="textarea textarea-bordered min-h-20 w-full" maxlength="2000" bind:value={closedMessage} aria-describedby="{formId}-closed-help"></textarea>
					<span id="{formId}-closed-help" class="label-text-alt mt-1 block {errs.closed_message ? 'text-error' : ''}">{errs.closed_message || t('shop.set.closed_message_help')}</span>
				</div>
			</section>
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.set.card.payments')}</h2>
				<p class="text-xs text-muted">{t('shop.set.payments_help')}</p>
				<div class="grid items-start gap-3 md:grid-cols-2">
					<div class="form-control w-full">
						<label class="label" for="{formId}-prov"><span class="label-text">{t('shop.set.provider')}</span></label>
						<select id="{formId}-prov" class="select select-bordered w-full" bind:value={provider}>
							<option value="">{t('shop.set.provider_none')}</option>
							{#each s.providers as p (p.key)}<option value={p.key}>{p.label}</option>{/each}
						</select>
						{#if errs.payment_provider}<span class="mt-1 block text-xs text-error">{errs.payment_provider}</span>{/if}
						{#if info}<span class="label-text-alt mt-1 block">{info.supports_refunds ? t('shop.set.refunds_yes') : t('shop.set.refunds_no')}</span>{/if}
					</div>
					<div class="form-control">
						<div class="label" aria-hidden="true"><span class="label-text">&nbsp;</span></div>
						<div class="flex h-9 items-center"><span class="badge {s.credentials_configured ? 'badge-success' : 'badge-warning'} gap-1"><KeyRound size={12} aria-hidden="true" /> {s.credentials_configured ? t('shop.set.configured') : t('shop.set.not_configured')}</span></div>
					</div>
				</div>
				{#if info && info.credential_fields.length}
					<h3 class="mt-2 font-display text-xs font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.set.credentials')}</h3>
					<div class="grid gap-3 md:grid-cols-2">
						{#each info.credential_fields as f (f)}
							<Field label={f} type="password" autocomplete="new-password" bind:value={() => creds[f] ?? '', (v) => (creds[f] = v)} placeholder={s.credentials_configured && provider === s.payment_provider ? t('shop.set.keep') : ''} disabled={clear} error={errs[`credentials.${f}`] ?? errs.credentials} />
						{/each}
					</div>
					<p class="text-xs text-muted">{t('shop.set.credentials_help')}</p>
					{#if s.credentials_configured}
						<label class="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={clear} /> {t('shop.set.clear')}</label>
					{/if}
				{/if}
			</section>
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.set.card.pickup')}</h2>
				<div class="form-control w-full">
					<label class="label" for="{formId}-pickup"><span class="label-text">{t('shop.set.pickup_label')}</span></label>
					<textarea id="{formId}-pickup" class="textarea textarea-bordered min-h-24 w-full" maxlength="2000" bind:value={pickupText} aria-describedby="{formId}-pickup-help"></textarea>
					<span id="{formId}-pickup-help" class="label-text-alt mt-1 block {errs.pickup_instructions ? 'text-error' : ''}">{errs.pickup_instructions || t('shop.set.pickup_help')}</span>
				</div>
				<div class="grid gap-1 rounded-lg border border-line p-3">
					<h3 class="font-display text-xs font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.set.card.payment_text')}</h3>
					{#if s.club_payment_instructions}<p class="text-sm whitespace-pre-line">{s.club_payment_instructions}</p>{:else}<p class="text-sm text-muted">{t('shop.set.payment_text_empty')}</p>{/if}
					<p class="text-xs text-muted">{t('shop.set.payment_text_help')}{#if data.me.can_manage_settings}<a class="link ml-1" href="/settings">{t('shop.set.payment_text_link')}</a>{/if}</p>
				</div>
			</section>
			<ShopNumbering rows={s.numbering} placeholders={s.numbering_placeholders} errors={errs} canEditInvoice={s.can_edit_invoice_numbering} bind:changes={numberingChanges} />
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.set.card.invoices')}</h2>
				<div class="grid gap-3 md:grid-cols-2">
					<Field label={t('shop.set.due_days')} type="number" min="0" max="365" bind:value={dueDays} error={errs.invoice_due_days} help={t('shop.set.due_days_help')} />
				</div>
			</section>
		</form>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.card.actions')}</h2>
				{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('shop.unsaved')}</p>{/if}
				<button class="btn btn-primary gap-2" type="submit" form={formId} disabled={busy || !dirty}><Save size={16} /> {t('common.save')}</button>
			</section>
			{#if info}
				<section class="card card-body gap-2">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.set.webhook')}</h2>
					<p class="text-xs text-muted">{t('shop.set.webhook_help')}</p>
					<input class="input input-bordered input-sm w-full font-mono text-xs" readonly value={webhook(info.key)} aria-label={t('shop.set.webhook')} onfocus={(e) => e.currentTarget.select()} />
				</section>
			{/if}
		</aside>
	</div>
</div>
