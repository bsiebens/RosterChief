<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { CalendarClock, Check, ClipboardList, Copy, ExternalLink, Link2, ReceiptText, Save, TriangleAlert } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { EVENT_KINDS, kindLabelKey } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import type { RegistrationSettings } from '#lib/registrations.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const s = $derived(data.settings);
	const draft = (stored: RegistrationSettings | null) => ({
		is_open: !!stored?.is_open,
		intro_text: stored?.intro_text ?? '',
		terms_text: stored?.terms_text ?? '',
		payment_instructions: stored?.payment_instructions ?? '',
		invoice_due_days: stored?.invoice_due_days ?? 30,
		installment_offsets: stored?.installment_offsets.join(', ') ?? '',
		installment_grace_days: stored?.installment_grace_days ?? 0,
		installment_block_kinds: [...(stored?.installment_block_kinds ?? [])]
	});
	// svelte-ignore state_referenced_locally
	let v = $state(draft(data.settings));
	$effect(() => {
		const stored = data.settings;
		if (!stored) return;
		untrack(() => (v = draft(stored)));
	});
	const dirty = $derived(!!s && JSON.stringify(v) !== JSON.stringify(draft(s)));
	const errs = $derived((form && 'fields' in form ? form.fields : {}) as Record<string, string>);
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const errorText = $derived(form && 'message' in form && form.message && !Object.keys(errs).some((k) => k !== '__all__') ? String(form.message) : '');
	let busy = $state(false);
	let copied = $state(false);
	async function copy() {
		try {
			await navigator.clipboard.writeText(data.joinUrl);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			/* the link is selectable text anyway */
		}
	}

	usePageHeader(() => ({ title: t('regset.title'), context: t('regset.context') }));
</script>

<svelte:head><title>{pageTitle(t('regset.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if done === 'save'}<Alert kind="success">{t('regset.saved')}</Alert>{/if}
	{#if errorText}<Alert kind="error" title={t('regset.save_failed')}>{errorText}</Alert>{/if}
	{#if data.error || !s}
		<Alert kind="error">{t('common.error.generic')}</Alert>
	{:else}
		{#if !v.is_open && !s.is_open}<Alert kind="warning" title={t('regset.warn_closed_title')}>{t('regset.warn_closed')}</Alert>{/if}
		{#if data.offerable === 0}<Alert kind="warning" title={t('regset.warn_products_title')}>{t('regset.warn_products')} <a class="link font-semibold" href="/shop/products">{t('regset.warn_products_link')}</a></Alert>{/if}

		<form method="POST" action="?/save" class="grid gap-5" use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; }; }}>
			<div class="grid items-start gap-5 xl:grid-cols-3">
				<div class="grid content-start gap-5 xl:col-span-2">
					<section class="card card-body gap-3">
	<div class="card-h -mx-[1.125rem] -mt-[1.125rem] mb-1"><h2 class="flex items-center gap-2"><ClipboardList size={16} aria-hidden="true" /> {t('regset.card.page')}</h2></div>
						<label class="flex items-start gap-3 rounded-lg border border-line p-3">
							<input type="checkbox" class="toggle mt-0.5" name="is_open" bind:checked={v.is_open} />
							<span class="grid gap-0.5"><span class="text-sm font-semibold text-ink">{t('regset.open')}</span><span class="text-xs text-muted">{t('regset.open_help')}</span></span>
						</label>
						<div class="form-control">
							<label class="label" for="intro"><span class="label-text">{t('regset.intro')}</span></label>
							<textarea id="intro" name="intro_text" class="textarea textarea-bordered min-h-24 w-full" bind:value={v.intro_text}></textarea>
							<span class="label-text-alt mt-1 block {errs.intro_text ? 'text-error' : ''}">{errs.intro_text || t('regset.intro_help')}</span>
						</div>
						<div class="form-control">
							<label class="label" for="terms"><span class="label-text">{t('regset.terms')}</span></label>
							<textarea id="terms" name="terms_text" class="textarea textarea-bordered min-h-24 w-full" bind:value={v.terms_text}></textarea>
							<span class="label-text-alt mt-1 block {errs.terms_text ? 'text-error' : ''}">{errs.terms_text || t('regset.terms_help')}</span>
						</div>
					</section>
					<section class="card card-body gap-3">
	<div class="card-h -mx-[1.125rem] -mt-[1.125rem] mb-1"><h2 class="flex items-center gap-2"><ReceiptText size={16} aria-hidden="true" /> {t('regset.card.invoice')}</h2></div>
						<div class="form-control">
							<label class="label" for="pay"><span class="label-text">{t('regset.payment')}</span></label>
							<textarea id="pay" name="payment_instructions" class="textarea textarea-bordered min-h-24 w-full" bind:value={v.payment_instructions}></textarea>
							<span class="label-text-alt mt-1 block {errs.payment_instructions ? 'text-error' : ''}">{errs.payment_instructions || t('regset.payment_help')}</span>
						</div>
						<div class="form-control max-w-xs">
							<label class="label" for="due"><span class="label-text">{t('regset.due_days')}</span></label>
							<input id="due" name="invoice_due_days" type="number" min="0" max="365" class="input input-bordered w-full {errs.invoice_due_days ? 'input-error' : ''}" bind:value={v.invoice_due_days} />
							<span class="label-text-alt mt-1 block {errs.invoice_due_days ? 'text-error' : ''}">{errs.invoice_due_days || t('regset.due_days_help')}</span>
						</div>
					</section>
					<section class="card card-body gap-3">
	<div class="card-h -mx-[1.125rem] -mt-[1.125rem] mb-1"><h2 class="flex items-center gap-2"><CalendarClock size={16} aria-hidden="true" /> {t('installments.title')}</h2></div>
						<div class="form-control">
							<label class="label" for="installment-offsets"><span class="label-text">{t('installments.offsets')}</span></label>
							<input id="installment-offsets" name="installment_offsets" class="input input-bordered w-full" aria-describedby="installment-offsets-help" aria-invalid={!!errs.installment_offsets} bind:value={v.installment_offsets} />
							<span id="installment-offsets-help" class="label-text-alt mt-1 block {errs.installment_offsets ? 'text-error' : ''}">{errs.installment_offsets || t('installments.offsets_help')}</span>
						</div>
						<div class="form-control max-w-xs">
							<label class="label" for="installment-grace"><span class="label-text">{t('installments.grace')}</span></label>
							<input id="installment-grace" name="installment_grace_days" type="number" min="0" max="365" class="input input-bordered w-full" aria-describedby="installment-grace-help" aria-invalid={!!errs.installment_grace_days} bind:value={v.installment_grace_days} />
							<span id="installment-grace-help" class="label-text-alt mt-1 block {errs.installment_grace_days ? 'text-error' : ''}">{errs.installment_grace_days || t('installments.grace_help')}</span>
						</div>
						<fieldset class="flex flex-wrap gap-x-5 gap-y-2">
							<legend class="mb-2 text-sm font-semibold">{t('installments.event_types')}</legend>
							{#each EVENT_KINDS as kind (kind)}
								<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox checkbox-sm" name="installment_block_kinds" value={kind} bind:group={v.installment_block_kinds} /> {t(kindLabelKey(kind))}</label>
							{/each}
							{#if errs.installment_block_kinds}<p class="basis-full text-xs text-error">{errs.installment_block_kinds}</p>{/if}
						</fieldset>
					</section>
				</div>

				<aside class="grid content-start gap-5 xl:sticky xl:top-20">
					<section class="card card-body gap-2">
						<div class="flex items-center justify-between gap-2">
							<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Link2 size={16} aria-hidden="true" /> {t('regset.card.link')}</h2>
							{#if s.is_open && data.offerable}<span class="badge badge-sm badge-success">{t('regset.status_open')}</span>
							{:else}<span class="badge badge-sm badge-warning gap-1"><TriangleAlert size={10} aria-hidden="true" /> {t('regset.status_closed')}</span>{/if}
						</div>
						<p class="text-xs text-muted">{t('regset.link_help')}</p>
						<div class="flex items-center gap-2">
							<input class="input input-bordered input-sm min-w-0 flex-1 font-mono text-xs" readonly value={data.joinUrl} aria-label={t('regset.card.link')} onfocus={(e) => e.currentTarget.select()} />
							<button class="btn btn-outline btn-sm gap-1" type="button" onclick={copy}>{#if copied}<Check size={14} /> {t('forms.send.copied')}{:else}<Copy size={14} /> {t('forms.send.copy')}{/if}</button>
							<a class="btn btn-outline btn-sm gap-1" href={data.joinUrl} target="_blank" rel="noopener"><ExternalLink size={14} aria-hidden="true" /> {t('regset.open_link')}</a>
						</div>
						{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('forms.unsaved')}</p>{/if}
						<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy || !dirty}><Save size={16} /> {t('common.save')}</button>
					</section>
				</aside>
			</div>
		</form>
	{/if}
</div>
