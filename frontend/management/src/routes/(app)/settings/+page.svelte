<script lang="ts">
	import { pageTitle } from '#lib/title.svelte.ts';
	import { enhance } from '$app/forms';
	import { Building2, Check, ExternalLink, Eye, FileText, Image as ImageIcon, Mail, MapPin, Palette, Save, Wallet, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ColorField from '#lib/components/ColorField.svelte';
	import Field from '#lib/components/Field.svelte';
	import Tabs from '#lib/components/Tabs.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { withPageMargins } from '#lib/pdf-preview.ts';

	let { data, form } = $props();

	usePageHeader(() => ({ title: t('settings.title'), actions: data.tab === 'identity' && data.settings ? actions : null }));

	const tabs = $derived([
		{ key: 'identity', label: t('settings.tab.identity') },
		{ key: 'email', label: t('settings.tab.email') },
		{ key: 'pdf', label: t('settings.tab.pdf') }
	]);

	// ---- Identity
	const s = $derived(data.settings);
	const DETAIL_KEYS = ['name', 'legal_name', 'contact_email', 'website', 'legal_address', 'legal_zip_code', 'legal_city', 'legal_country', 'vat_id', 'payment_instructions'] as const;
	// svelte-ignore state_referenced_locally
	let details = $state<Record<string, string>>(Object.fromEntries(DETAIL_KEYS.map((k) => [k, (data.settings as any)?.[k] ?? ''])));
	// svelte-ignore state_referenced_locally
	let colors = $state({
		primary_color: data.settings?.primary_color ?? '',
		secondary_color: data.settings?.secondary_color ?? '',
		primary_text_color: data.settings?.primary_text_color ?? '',
		secondary_text_color: data.settings?.secondary_text_color ?? ''
	});
	let busy = $state(false);
	let logoPreview = $state<string | null>(null);
	let removeLogo = $state(false);
	const logoUrl = $derived(logoPreview ?? (removeLogo ? null : (s?.logo_url ?? null)));

	function onLogo(e: Event & { currentTarget: HTMLInputElement }) {
		if (logoPreview) URL.revokeObjectURL(logoPreview);
		const file = e.currentTarget.files?.[0];
		logoPreview = file ? URL.createObjectURL(file) : null;
		if (file) removeLogo = false;
	}
	const fe = (name: string) => (form?.settingsAction === 'save' ? form.settingsFields?.[name] : undefined);
	const generalError = $derived(form?.settingsAction === 'save' && form.settingsError && !Object.keys(form.settingsFields ?? {}).length ? (form.settingsError as string) : null);

	const submit = () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') {
				logoPreview = null;
				removeLogo = false;
			}
		};
	};

	// ---- Contrast (WCAG): the text colour that actually lands on each brand colour, chosen automatically when left empty.
	const HEX = /^#[0-9a-f]{6}$/;
	const lum = (hex: string) => {
		const [r, g, b] = [1, 3, 5].map((i) => {
			const c = parseInt(hex.slice(i, i + 2), 16) / 255;
			return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
		});
		return 0.2126 * r + 0.7152 * g + 0.0722 * b;
	};
	const ratio = (a: string, b: string) => {
		const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
		return (x + 0.05) / (y + 0.05);
	};
	const check = (bg: string, text: string) => {
		if (!HEX.test(bg)) return null;
		const fg = HEX.test(text) ? text : ratio(bg, '#ffffff') >= ratio(bg, '#000000') ? '#ffffff' : '#000000';
		return { fg, value: ratio(bg, fg) };
	};
	const onPrimary = $derived(check(colors.primary_color, colors.primary_text_color));
	const onSecondary = $derived(check(colors.secondary_color, colors.secondary_text_color));
	const primaryBg = $derived(HEX.test(colors.primary_color) ? colors.primary_color : 'var(--color-primary)');
	const primaryFg = $derived(onPrimary?.fg ?? 'var(--color-primary-content)');
	const secondaryBg = $derived(HEX.test(colors.secondary_color) ? colors.secondary_color : 'var(--color-club)');
	const secondaryFg = $derived(onSecondary?.fg ?? '#ffffff');
	const initials = $derived(
		details.name
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((w) => w[0]?.toUpperCase())
			.join('')
	);

	// ---- Previews
	const kind = $derived(data.tab === 'pdf' ? 'pdf' : 'email');
	let selected = $state<string | null>(null);
	const list = $derived(data.previews.filter((p) => p.kind === kind));
	const current = $derived(data.previews.find((p) => p.key === selected) ?? null);
	let pv = $state<{ loading: boolean; failed: boolean; html: string; subject: string; pdfUrl: string | null; pdfUnavailable: boolean }>({ loading: false, failed: false, html: '', subject: '', pdfUrl: null, pdfUnavailable: false });

	$effect(() => {
		const item = current;
		if (!item) return;
		const ctl = new AbortController();
		let blob: string | null = null;
		pv = { loading: true, failed: false, html: '', subject: '', pdfUrl: null, pdfUnavailable: false };
		const base = `/settings/preview/${encodeURIComponent(item.key)}`;
		(async () => {
			try {
				const [h, f] = await Promise.all([fetch(`${base}/html`, { signal: ctl.signal }), item.kind === 'pdf' ? fetch(`${base}/file`, { signal: ctl.signal }) : Promise.resolve(null)]);
				const next = { loading: false, failed: !h.ok, html: h.ok ? await h.text() : '', subject: '', pdfUrl: null as string | null, pdfUnavailable: false };
				const raw = h.headers.get('x-preview-subject');
				if (raw) {
					try {
						next.subject = decodeURIComponent(raw);
					} catch {
						next.subject = raw;
					}
				}
				if (f) {
					if (f.ok) next.pdfUrl = blob = URL.createObjectURL(await f.blob());
					else next.pdfUnavailable = true;
				}
				pv = next;
			} catch (e) {
				if ((e as Error).name !== 'AbortError') pv = { ...pv, loading: false, failed: true };
			}
		})();
		return () => {
			ctl.abort();
			if (blob) URL.revokeObjectURL(blob);
		};
	});
	// A tab switch keeps the page component alive: forget the previous selection.
	$effect(() => {
		data.tab;
		selected = null;
	});
</script>

<svelte:head><title>{pageTitle(t('settings.title'))}</title></svelte:head>

{#snippet actions()}
	<button class="btn btn-primary gap-2" type="submit" form="settings-form" disabled={busy}><Save size={16} /> {t('settings.save_changes')}</button>
{/snippet}

{#snippet card(title: string, icon: typeof Building2, body: import('svelte').Snippet, aside?: string)}
	{@const Icon = icon}
	<section class="card card-body gap-3">
		<div class="flex items-center justify-between gap-2">
			<h2 class="card-title"><Icon size={18} /> {title}</h2>
			{#if aside}<span class="font-mono text-xs text-muted">{aside}</span>{/if}
		</div>
		{@render body()}
	</section>
{/snippet}

<!-- Full-bleed like the members list: a filter-bar strip with the toggle on the right, content below. -->
<div class="-mx-7 -my-6 flex flex-col">
<div class="flex flex-wrap items-center gap-3 border-b border-line bg-white px-7 py-2.5">
	<div class="flex-1"></div>
	<Tabs {tabs} active={data.tab} label={t('settings.tabs')} />
</div>

<div class="grid gap-5 px-7 py-5">

	{#if data.error}
		<Alert kind="error">{t('settings.load_failed')}</Alert>
	{:else if data.tab === 'identity' && s}
		{#if form?.settingsAction === 'save' && form.saved}<Alert kind="success">{t('settings.saved')}</Alert>{/if}
		{#if generalError}<Alert kind="error" title={t('settings.save_failed')}>{generalError}</Alert>{/if}

		<form id="settings-form" method="POST" action="?/save" enctype="multipart/form-data" class="grid items-start gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]" use:enhance={submit}>
			<div class="grid gap-5">
				{#snippet clubBody()}
					<div class="grid gap-3 sm:grid-cols-2">
						<Field label={t('settings.f.name')} name="name" required bind:value={details.name} error={fe('name')} />
						<Field label={t('settings.f.legal_name')} name="legal_name" bind:value={details.legal_name} error={fe('legal_name')} help={t('settings.legal_name_help')} />
						<Field label={t('settings.f.contact_email')} name="contact_email" type="email" bind:value={details.contact_email} error={fe('contact_email')} help={t('settings.contact_email_help')} />
						<Field label={t('settings.f.website')} name="website" type="url" bind:value={details.website} error={fe('website')} help={t('settings.website_help')} />
					</div>
				{/snippet}
				{@render card(t('settings.card.club'), Building2, clubBody)}

				{#snippet addressBody()}
					<div class="grid gap-3 sm:grid-cols-2">
						<div class="sm:col-span-2"><Field label={t('settings.f.legal_address')} name="legal_address" bind:value={details.legal_address} error={fe('legal_address')} help={t('settings.legal_address_help')} /></div>
						<Field label={t('settings.f.legal_zip_code')} name="legal_zip_code" bind:value={details.legal_zip_code} error={fe('legal_zip_code')} />
						<Field label={t('settings.f.legal_city')} name="legal_city" bind:value={details.legal_city} error={fe('legal_city')} />
						<div class="form-control w-full">
							<label class="label" for="legal_country"><span class="label-text">{t('settings.f.legal_country')}</span></label>
							<select id="legal_country" name="legal_country" class="select select-bordered w-full {fe('legal_country') ? 'input-error' : ''}" bind:value={details.legal_country}>
								<option value="">—</option>
								{#each data.countries as c (c.code)}<option value={c.code}>{c.name}</option>{/each}
							</select>
							{#if fe('legal_country')}<span class="mt-1 block text-xs text-error">{fe('legal_country')}</span>{/if}
						</div>
						<Field label={t('settings.f.vat_id')} name="vat_id" bind:value={details.vat_id} error={fe('vat_id')} help={t('settings.vat_help')} />
					</div>
				{/snippet}
				{@render card(t('settings.card.address'), MapPin, addressBody)}

				{#snippet paymentBody()}
					<div class="form-control w-full">
						<label class="label" for="payment_instructions"><span class="label-text">{t('settings.f.payment_instructions')}</span></label>
						<textarea id="payment_instructions" name="payment_instructions" rows="4" class="textarea textarea-bordered w-full {fe('payment_instructions') ? 'input-error' : ''}" bind:value={details.payment_instructions}></textarea>
						{#if fe('payment_instructions')}<span class="mt-1 block text-xs text-error">{fe('payment_instructions')}</span>{:else}<span class="label-text-alt mt-1 block">{t('settings.f.payment_instructions_help')}</span>{/if}
					</div>
				{/snippet}
				{@render card(t('settings.card.payment'), Wallet, paymentBody)}
			</div>

			<div class="grid gap-5">
				{#snippet logoBody()}
					<div class="flex flex-wrap items-center gap-4">
						{#if logoUrl}
							<img src={logoUrl} alt={t('settings.logo')} class="h-20 w-20 rounded-lg border border-line bg-white object-contain p-1" />
						{:else}
							<div class="flex h-20 w-20 flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed border-rule text-muted"><ImageIcon size={22} /><span class="text-[10px]">{t('settings.logo.none')}</span></div>
						{/if}
						<div class="min-w-0 flex-1">
							<input id="logo" type="file" name="logo" aria-label={t('settings.logo')} accept="image/jpeg,image/png,image/webp" class="file-input file-input-bordered file-input-sm w-full {fe('logo') ? 'input-error' : ''}" onchange={onLogo} />
							{#if fe('logo')}<span class="mt-1 block text-xs text-error">{fe('logo')}</span>{:else}<span class="label-text-alt mt-1 block">{t('settings.logo.help')}</span>{/if}
							{#if s.logo_url && !logoPreview}
								<label class="label mt-1 cursor-pointer justify-start gap-2">
									<input type="checkbox" name="remove_logo" class="checkbox checkbox-sm" bind:checked={removeLogo} />
									<span class="label-text">{t('settings.logo.remove')}</span>
								</label>
							{/if}
						</div>
					</div>
				{/snippet}
				{@render card(t('settings.card.logo'), ImageIcon, logoBody)}

				{#snippet coloursBody()}
					<div class="grid gap-3 sm:grid-cols-2">
						<ColorField label={t('settings.primary_color')} name="primary_color" bind:value={colors.primary_color} error={fe('primary_color')} />
						<ColorField label={t('settings.secondary_color')} name="secondary_color" bind:value={colors.secondary_color} error={fe('secondary_color')} />
						<ColorField label={t('settings.primary_text_color')} name="primary_text_color" bind:value={colors.primary_text_color} placeholder={t('settings.text_auto')} error={fe('primary_text_color')} />
						<ColorField label={t('settings.secondary_text_color')} name="secondary_text_color" bind:value={colors.secondary_text_color} placeholder={t('settings.text_auto')} error={fe('secondary_text_color')} />
					</div>
					<p class="label-text-alt">{t('settings.text_help')}</p>
					<h3 class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('settings.contrast')}</h3>
					<div class="grid gap-2 sm:grid-cols-2">
						{#each [[t('settings.contrast.on_primary'), primaryBg, onPrimary], [t('settings.contrast.on_secondary'), secondaryBg, onSecondary]] as [label, bg, c] (label)}
							<div class="flex items-center justify-between gap-2 rounded-md px-3 py-2 text-sm font-semibold" style="background:{bg};color:{(c as any)?.fg ?? '#ffffff'}">
								<span>{label}</span>
								{#if c}
									<span class="flex items-center gap-1 font-mono text-xs">{(c as any).value.toFixed(1)} {#if (c as any).value >= 4.5}<Check size={14} />{:else}<X size={14} />{/if}</span>
								{/if}
							</div>
						{/each}
					</div>
				{/snippet}
				{@render card(t('settings.card.colours'), Palette, coloursBody, t('settings.accents'))}

				{#snippet previewBody()}
					<div class="overflow-hidden rounded-lg border border-line">
						<div class="flex items-center justify-between gap-2 px-3 py-2" style="background:{primaryBg};color:{primaryFg}">
							<span class="flex min-w-0 items-center gap-2">
								{#if logoUrl}<img src={logoUrl} alt="" class="h-7 w-7 rounded-full bg-white object-contain" />{:else}<span class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold" style="background:{secondaryBg};color:{secondaryFg}">{initials || '?'}</span>{/if}
								<span class="truncate font-display text-sm font-extrabold uppercase">{details.name || '—'}</span>
							</span>
							<span class="rounded-md px-2 py-1 text-xs font-bold" style="background:{secondaryBg};color:{secondaryFg}">{t('settings.preview.button')}</span>
						</div>
						<div class="grid grid-cols-[8rem_1fr]">
							<div class="grid gap-1 p-2 text-xs font-semibold" style="background:{primaryBg};color:{primaryFg}">
								<span class="rounded px-2 py-1 opacity-80">{t('settings.preview.nav')}</span>
								<span class="rounded px-2 py-1" style="background:{secondaryBg};color:{secondaryFg}">{t('settings.preview.nav_active')}</span>
							</div>
							<div class="grid gap-2 bg-white p-3">
								<div class="h-2 w-3/4 rounded bg-rule"></div>
								<div class="h-2 w-1/2 rounded bg-rule"></div>
							</div>
						</div>
					</div>
					<h3 class="font-display text-xs font-bold tracking-[.1em] text-muted uppercase">{t('settings.preview.hint')}</h3>
					<ul class="grid gap-1.5 text-sm">
						{#each [t('settings.preview.l1'), t('settings.preview.l2'), t('settings.preview.l3')] as line (line)}
							<li class="flex items-center gap-2"><span class="h-2 w-2 shrink-0" style="background:{secondaryBg}"></span>{line}</li>
						{/each}
						<li class="flex items-center gap-2 text-muted"><span class="h-2 w-2 shrink-0 bg-rule"></span>{t('settings.preview.l4')}</li>
					</ul>
				{/snippet}
				{@render card(t('settings.card.preview'), Eye, previewBody)}
			</div>
		</form>
	{:else if data.tab !== 'identity'}
		<Alert kind="info">{t('settings.previews.hint')}</Alert>
		{#if !list.length}
			<div class="card card-body items-center py-10 text-center text-muted"><p>{t('settings.previews.empty')}</p></div>
		{:else}
			<div class="grid gap-4 lg:grid-cols-[18rem_1fr]">
				<ul class="card divide-y divide-line self-start overflow-hidden">
					{#each list as p (p.key)}
						<li>
							<button type="button" class="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-surface {selected === p.key ? 'bg-surface' : ''}" aria-current={selected === p.key ? 'true' : undefined} onclick={() => (selected = p.key)}>
								<span class="mt-0.5 text-muted">{#if p.kind === 'email'}<Mail size={16} />{:else}<FileText size={16} />{/if}</span>
								<span class="min-w-0">
									<span class="block font-semibold text-ink">{p.title}</span>
									<span class="block text-xs text-muted">{p.description}</span>
								</span>
							</button>
						</li>
					{/each}
				</ul>

				<div class="card card-body gap-3">
					{#if !current}
						<p class="text-muted">{t('settings.previews.pick')}</p>
					{:else}
						<div class="flex flex-wrap items-center justify-between gap-2">
							<h2 class="card-title">{current.title}</h2>
							{#if current.kind === 'pdf' && pv.pdfUrl}
								<a class="btn btn-outline btn-sm gap-2" href={pv.pdfUrl} target="_blank" rel="noopener"><ExternalLink size={14} /> {t('settings.previews.open_pdf')}</a>
							{/if}
						</div>
						{#if pv.loading}
							<p class="text-muted">{t('settings.previews.loading')}</p>
						{:else if pv.failed}
							<Alert kind="error">{t('settings.previews.failed')}</Alert>
						{:else}
							{#if current.kind === 'email' && pv.subject}
								<p class="text-sm"><span class="font-display text-xs font-bold tracking-wide text-muted uppercase">{t('settings.previews.subject')}</span> <span class="font-semibold text-ink">{pv.subject}</span></p>
							{/if}
							{#if pv.pdfUnavailable}<Alert kind="warning">{t('settings.previews.pdf_unavailable')}</Alert>{/if}
							<iframe title={t('settings.previews.frame', { title: current.title })} sandbox="" srcdoc={current.kind === 'pdf' ? withPageMargins(pv.html) : pv.html} class="h-[40rem] w-full rounded-lg border border-line bg-white"></iframe>
						{/if}
					{/if}
				</div>
			</div>
		{/if}
	{/if}
</div>
</div>
