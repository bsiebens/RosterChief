<script lang="ts">
	import { goto } from '$app/navigation';
	import { CircleAlert, Lock, Plus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import PublicShell from '#lib/components/registration/PublicShell.svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { blankEntry, entryBody, statusHref, type EntryDraft, type Preview } from '#lib/registration-public.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	const cfg = $derived(data.config);

	// What each kind of registration may choose from: products in the Player / Volunteer category only for that kind, the rest for both.
	const productsFor = (kind: string) => cfg.products.filter((p) => !p.entry_kind || p.entry_kind === kind);
	const groupedFor = (kind: string) => productsFor(kind).map((p) => ({ product: p, variants: p.variants }));
	const variantsFor = (kind: string) => productsFor(kind).flatMap((p) => p.variants);
	/** Keep the chosen option valid for the person's kind: drop an incompatible choice, and pick the only option when there is just one. */
	function reconcile(e: EntryDraft) {
		const options = variantsFor(e.kind);
		if (e.variant_id && !options.some((v) => v.id === e.variant_id)) e.variant_id = '';
		if (!e.variant_id && options.length === 1) e.variant_id = options[0].id;
	}
	const fresh = (patch: Partial<EntryDraft> = {}): EntryDraft => {
		const e = { ...blankEntry(''), ...patch };
		reconcile(e);
		return e;
	};

	let contact = $state({ first_name: '', last_name: '', email: '', phone: '' });
	// svelte-ignore state_referenced_locally
	let entries = $state<EntryDraft[]>([fresh({ who: 'self' })]);
	let website = $state('');
	let accepted = $state(false);
	let busy = $state(false);
	let errors = $state<Record<string, string>>({});
	let formError = $state('');
	let closedNow = $state(false);
	let preview = $state<Preview | null>(null);
	let previewError = $state('');

	const err = (key: string) => errors[key] ?? '';
	const eerr = (i: number, name: string) => errors[`entries.${i}.${name}`] ?? '';
	const when = (iso: string) => new Intl.DateTimeFormat(getLang(), { dateStyle: 'long' }).format(new Date(iso));

	// Live price: whenever every person has chosen what to register for, ask for the price (debounced; nothing is saved).
	let seq = 0;
	$effect(() => {
		const ready = entries.length > 0 && entries.every((e) => e.variant_id);
		const body = JSON.stringify({ entries: entries.map(entryBody) });
		if (!ready) {
			preview = null;
			return;
		}
		const mine = ++seq;
		const timer = setTimeout(async () => {
			const res = await api<Preview>('POST', '/public/registration/preview', { body: JSON.parse(body) });
			if (mine !== seq) return;
			previewError = res.ok ? '' : res.status === 429 ? t('reg.err.rate_limited') : t('reg.err.preview');
			preview = res.ok ? (res.data ?? null) : null;
		}, 400);
		return () => clearTimeout(timer);
	});

	function addPerson() {
		if (entries.length >= cfg.max_entries) return;
		entries = [...entries, fresh()];
	}
	const removePerson = (uid: number) => (entries = entries.filter((e) => e.uid !== uid));
	// Only one person can be "myself"; a second one is a child.
	const hasSelf = (uid: number) => entries.some((e) => e.who === 'self' && e.uid !== uid);

	const summary = $derived(Object.entries(errors).filter(([, m]) => !!m));

	function clientErrors(): Record<string, string> {
		const out: Record<string, string> = {};
		const need = t('reg.err.required');
		if (!contact.first_name.trim()) out['contact.first_name'] = need;
		if (!contact.last_name.trim()) out['contact.last_name'] = need;
		if (!contact.email.trim()) out['contact.email'] = need;
		entries.forEach((e, i) => {
			if (!e.variant_id) out[`entries.${i}.variant_id`] = t('reg.err.choose_offer');
			if (e.who === 'child') {
				if (!e.first_name.trim()) out[`entries.${i}.first_name`] = need;
				if (!e.last_name.trim()) out[`entries.${i}.last_name`] = need;
				if (!e.date_of_birth) out[`entries.${i}.date_of_birth`] = need;
			}
		});
		if (cfg.terms_text && !accepted) out.terms = t('reg.err.terms');
		return out;
	}

	async function submit(ev: SubmitEvent) {
		ev.preventDefault();
		if (busy) return;
		formError = '';
		const local = clientErrors();
		errors = local;
		if (Object.keys(local).length) {
			queueMicrotask(() => document.getElementById('reg-errors')?.focus());
			return;
		}
		busy = true;
		const res = await api<{ token: string }>('POST', '/public/registration', { body: { contact: { ...contact, first_name: contact.first_name.trim(), last_name: contact.last_name.trim(), email: contact.email.trim(), phone: contact.phone.trim() }, entries: entries.map(entryBody), website, language: getLang() } });
		busy = false;
		if (res.ok && res.data) {
			await goto(statusHref(res.data.token));
			return;
		}
		if (res.code === 'closed') closedNow = true;
		else if (res.status === 429 || res.code === 'rate_limited') formError = t('reg.err.rate_limited');
		else if (res.status === 0) formError = t('reg.err.network');
		else if (Object.keys(res.fields).length) {
			errors = res.fields;
			formError = t('reg.err.fix');
		} else formError = res.message || t('reg.err.generic');
		queueMicrotask(() => document.getElementById('reg-errors')?.focus());
	}
</script>

<svelte:head>
	<title>{pageTitle(t('reg.title'))}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<PublicShell title={t('reg.title')} kicker={t('reg.kicker')} club={cfg.club} wide>
	{#if !cfg.is_open || closedNow}
		<div class="card card-body items-center gap-2 py-10 text-center" role="status">
			<Lock size={32} class="text-dim" aria-hidden="true" />
			<p class="font-display text-xl font-bold text-ink">{t('reg.closed.title')}</p>
			<p class="text-muted">{t('reg.closed.body')}</p>
			{#if cfg.intro_text}<p class="mt-2 whitespace-pre-line text-sm text-muted">{cfg.intro_text}</p>{/if}
		</div>
	{:else}
		<form class="grid gap-4" onsubmit={submit} novalidate>
			{#if cfg.intro_text}<p class="whitespace-pre-line text-muted">{cfg.intro_text}</p>{/if}
			<p class="text-xs text-muted">{t('reg.required_hint')}</p>

			<div id="reg-errors" tabindex="-1" class="outline-none" role={formError || summary.length ? 'alert' : undefined}>
				{#if formError || summary.length}
					<Alert kind="error" title={formError || t('reg.err.fix')}>
						{#if summary.length}<ul class="list-disc pl-5 text-sm">{#each summary as [key, msg] (key)}<li>{msg}</li>{/each}</ul>{/if}
					</Alert>
				{/if}
			</div>

			<section class="card card-body gap-4" aria-labelledby="reg-contact">
				<div>
					<h2 id="reg-contact" class="font-display text-base font-bold text-ink">{t('reg.contact.title')}</h2>
					<p class="text-sm text-muted">{t('reg.contact.help')}</p>
				</div>
				<div class="grid gap-4 sm:grid-cols-2">
					<div class="form-control">
						<label class="label" for="c-first"><span class="label-text">{t('reg.f.first_name')}<span class="text-error" aria-hidden="true"> *</span></span></label>
						<input id="c-first" class="input input-bordered w-full {err('contact.first_name') ? 'input-error' : ''}" autocomplete="given-name" bind:value={contact.first_name} aria-invalid={err('contact.first_name') ? 'true' : undefined} />
						{#if err('contact.first_name')}<span class="mt-1 block text-xs text-error">{err('contact.first_name')}</span>{/if}
					</div>
					<div class="form-control">
						<label class="label" for="c-last"><span class="label-text">{t('reg.f.last_name')}<span class="text-error" aria-hidden="true"> *</span></span></label>
						<input id="c-last" class="input input-bordered w-full {err('contact.last_name') ? 'input-error' : ''}" autocomplete="family-name" bind:value={contact.last_name} aria-invalid={err('contact.last_name') ? 'true' : undefined} />
						{#if err('contact.last_name')}<span class="mt-1 block text-xs text-error">{err('contact.last_name')}</span>{/if}
					</div>
					<div class="form-control">
						<label class="label" for="c-email"><span class="label-text">{t('reg.f.email')}<span class="text-error" aria-hidden="true"> *</span></span></label>
						<input id="c-email" type="email" class="input input-bordered w-full {err('contact.email') ? 'input-error' : ''}" autocomplete="email" bind:value={contact.email} aria-invalid={err('contact.email') ? 'true' : undefined} />
						{#if err('contact.email')}<span class="mt-1 block text-xs text-error">{err('contact.email')}</span>{:else}<span class="label-text-alt mt-1 block">{t('reg.f.email_help')}</span>{/if}
					</div>
					<div class="form-control">
						<label class="label" for="c-phone"><span class="label-text">{t('reg.f.phone')}</span></label>
						<input id="c-phone" type="tel" class="input input-bordered w-full {err('contact.phone') ? 'input-error' : ''}" autocomplete="tel" bind:value={contact.phone} />
						{#if err('contact.phone')}<span class="mt-1 block text-xs text-error">{err('contact.phone')}</span>{/if}
					</div>
				</div>
			</section>

			{#each entries as e, i (e.uid)}
				{@const fid = `e${e.uid}`}
				<section class="card card-body gap-4" aria-labelledby="{fid}-h">
					<div class="flex items-center justify-between gap-3">
						<h2 id="{fid}-h" class="font-display text-base font-bold text-ink">{t('reg.person.n', { n: i + 1 })}</h2>
						{#if entries.length > 1}<button type="button" class="btn btn-outline btn-error btn-xs gap-1" onclick={() => removePerson(e.uid)} aria-label={t('reg.person.remove_n', { n: i + 1 })}><Trash2 size={13} aria-hidden="true" /> {t('reg.person.remove')}</button>{/if}
					</div>

					<fieldset class="grid gap-2">
						<legend class="label-text mb-1">{t('reg.who.label')}</legend>
						<div class="flex flex-wrap gap-x-5 gap-y-2 text-sm">
							<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="{fid}-who" value="child" bind:group={e.who} /> {t('reg.who.child')}</label>
							<label class="flex items-center gap-2 {hasSelf(e.uid) ? 'opacity-50' : ''}"><input type="radio" class="radio radio-sm" name="{fid}-who" value="self" bind:group={e.who} disabled={hasSelf(e.uid)} /> {t('reg.who.self')}</label>
						</div>
					</fieldset>

					{#if e.who === 'child'}
						<div class="grid gap-4 sm:grid-cols-2">
							<div class="form-control">
								<label class="label" for="{fid}-first"><span class="label-text">{t('reg.f.first_name')}<span class="text-error" aria-hidden="true"> *</span></span></label>
								<input id="{fid}-first" class="input input-bordered w-full {eerr(i, 'first_name') ? 'input-error' : ''}" bind:value={e.first_name} aria-invalid={eerr(i, 'first_name') ? 'true' : undefined} />
								{#if eerr(i, 'first_name')}<span class="mt-1 block text-xs text-error">{eerr(i, 'first_name')}</span>{/if}
							</div>
							<div class="form-control">
								<label class="label" for="{fid}-last"><span class="label-text">{t('reg.f.last_name')}<span class="text-error" aria-hidden="true"> *</span></span></label>
								<input id="{fid}-last" class="input input-bordered w-full {eerr(i, 'last_name') ? 'input-error' : ''}" bind:value={e.last_name} aria-invalid={eerr(i, 'last_name') ? 'true' : undefined} />
								{#if eerr(i, 'last_name')}<span class="mt-1 block text-xs text-error">{eerr(i, 'last_name')}</span>{/if}
							</div>
							<div class="form-control">
								<label class="label" for="{fid}-dob"><span class="label-text">{t('reg.f.dob')}<span class="text-error" aria-hidden="true"> *</span></span></label>
								<input id="{fid}-dob" type="date" class="input input-bordered w-full {eerr(i, 'date_of_birth') ? 'input-error' : ''}" bind:value={e.date_of_birth} aria-invalid={eerr(i, 'date_of_birth') ? 'true' : undefined} />
								{#if eerr(i, 'date_of_birth')}<span class="mt-1 block text-xs text-error">{eerr(i, 'date_of_birth')}</span>{/if}
							</div>
							<div class="form-control">
								<label class="label" for="{fid}-email"><span class="label-text">{t('reg.f.child_email')}</span></label>
								<input id="{fid}-email" type="email" class="input input-bordered w-full" bind:value={e.email} />
								{#if eerr(i, 'email')}<span class="mt-1 block text-xs text-error">{eerr(i, 'email')}</span>{/if}
							</div>
						</div>
					{:else}
						<div class="grid gap-4 sm:grid-cols-2">
							<div class="form-control">
								<label class="label" for="{fid}-dob"><span class="label-text">{t('reg.f.dob')}</span></label>
								<input id="{fid}-dob" type="date" class="input input-bordered w-full" bind:value={e.date_of_birth} />
								{#if eerr(i, 'date_of_birth')}<span class="mt-1 block text-xs text-error">{eerr(i, 'date_of_birth')}</span>{/if}
							</div>
						</div>
						<p class="-mt-2 text-xs text-muted">{t('reg.who.self_help')}</p>
					{/if}

					<fieldset class="grid gap-2">
						<legend class="label-text mb-1">{t('reg.kind.label')}</legend>
						<div class="flex flex-wrap gap-x-5 gap-y-2 text-sm">
							<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="{fid}-kind" value="player" bind:group={e.kind} onchange={() => reconcile(e)} /> {t('reg.kind.player')}</label>
							<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="{fid}-kind" value="volunteer" bind:group={e.kind} onchange={() => reconcile(e)} /> {t('reg.kind.volunteer')}</label>
						</div>
					</fieldset>

					<div class="form-control">
						<label class="label" for="{fid}-offer"><span class="label-text">{t('reg.f.offer')}<span class="text-error" aria-hidden="true"> *</span></span></label>
						{#if variantsFor(e.kind).length}
							<select id="{fid}-offer" class="select select-bordered w-full {eerr(i, 'variant_id') ? 'select-error' : ''}" bind:value={e.variant_id} aria-invalid={eerr(i, 'variant_id') ? 'true' : undefined}>
								<option value="">{t('reg.f.offer_pick')}</option>
								{#each groupedFor(e.kind) as g (g.product.id)}
									<optgroup label={g.product.name}>
										{#each g.variants as v (v.id)}<option value={v.id}>{v.name === g.product.name ? g.product.name : v.name} · {money(v.price)}</option>{/each}
									</optgroup>
								{/each}
							</select>
						{:else}
							<p id="{fid}-offer" class="rounded-lg border border-dashed border-line px-3 py-2 text-sm text-muted" role="status">{t(e.kind === 'volunteer' ? 'reg.f.offer_none_volunteer' : 'reg.f.offer_none_player')}</p>
						{/if}
						{#if eerr(i, 'variant_id')}<span class="mt-1 block text-xs text-error">{eerr(i, 'variant_id')}</span>{/if}
					</div>

					{#if e.kind === 'player'}
						<div class="grid gap-4 sm:grid-cols-3">
							<div class="form-control">
								<label class="label" for="{fid}-team"><span class="label-text">{t('reg.f.team')}</span></label>
								<select id="{fid}-team" class="select select-bordered w-full" bind:value={e.requested_team_id}>
									<option value="">{t('reg.f.no_preference')}</option>
									{#each cfg.teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
								</select>
							</div>
							<div class="form-control">
								<label class="label" for="{fid}-pos"><span class="label-text">{t('reg.f.position')}</span></label>
								<select id="{fid}-pos" class="select select-bordered w-full" bind:value={e.requested_position_id}>
									<option value="">{t('reg.f.no_preference')}</option>
									{#each cfg.positions as pos (pos.id)}<option value={pos.id}>{pos.name}</option>{/each}
								</select>
							</div>
							<div class="form-control">
								<label class="label" for="{fid}-jersey"><span class="label-text">{t('reg.f.jersey')}</span></label>
								<input id="{fid}-jersey" type="number" inputmode="numeric" min="0" max="99" class="input input-bordered w-full {eerr(i, 'requested_jersey_number') ? 'input-error' : ''}" bind:value={e.requested_jersey_number} />
								{#if eerr(i, 'requested_jersey_number')}<span class="mt-1 block text-xs text-error">{eerr(i, 'requested_jersey_number')}</span>{/if}
							</div>
						</div>
						<p class="-mt-2 text-xs text-muted">{t('reg.f.team_help')}</p>
					{/if}

					<label class="flex items-start gap-3 text-sm"><input type="checkbox" class="checkbox mt-0.5" bind:checked={e.photo_public_consent} /> <span>{t('reg.f.photo_consent')}<span class="block text-xs text-muted">{t('reg.f.photo_consent_help')}</span></span></label>
				</section>
			{/each}

			{#if entries.length < cfg.max_entries}
				<button type="button" class="btn btn-outline w-full gap-2 sm:w-auto sm:self-start" onclick={addPerson}><Plus size={16} aria-hidden="true" /> {t('reg.person.add')}</button>
			{/if}

			<section class="card card-body gap-3" aria-labelledby="reg-price" aria-live="polite">
				<h2 id="reg-price" class="font-display text-base font-bold text-ink">{t('reg.price.title')}</h2>
				{#if previewError}
					<p class="flex items-center gap-2 text-sm text-error"><CircleAlert size={16} aria-hidden="true" /> {previewError}</p>
				{:else if !preview}
					<p class="text-sm text-muted">{t('reg.price.pick')}</p>
				{:else}
					<ul class="divide-y divide-rule text-sm">
						{#each preview.lines as l (l.index)}
							<li class="flex flex-wrap items-baseline justify-between gap-x-4 py-2">
								<span><span class="font-semibold text-ink">{entries[l.index]?.who === 'self' ? `${contact.first_name} ${contact.last_name}`.trim() || t('reg.who.self') : `${entries[l.index]?.first_name ?? ''} ${entries[l.index]?.last_name ?? ''}`.trim() || t('reg.person.n', { n: l.index + 1 })}</span> <span class="text-muted">· {l.description}</span></span>
								<span class="font-mono">
									{#if Number(l.discount) > 0}<span class="mr-2 text-muted line-through">{money(l.base_price)}</span>{/if}{money(l.price)}
								</span>
								{#if Number(l.discount) > 0}<span class="w-full text-xs text-ok-text">{t('reg.price.discount', { amount: money(l.discount) })}</span>{/if}
							</li>
						{/each}
					</ul>
					<div class="flex items-baseline justify-between gap-4 border-t border-rule pt-3">
						<span class="font-display text-sm font-bold tracking-[.08em] text-ink uppercase">{t('reg.price.total')}</span>
						<span class="font-display text-2xl font-extrabold text-ink">{money(preview.total)}</span>
					</div>
					{#if Number(preview.early_bird_discount) > 0}
						{@const deadline = preview.lines.find((l) => l.early_bird_deadline)?.early_bird_deadline}
						<p class="rounded-md bg-ok-bg px-3 py-2 text-sm text-ok-text">{t('reg.price.early', { amount: money(preview.early_bird_discount), total: money(preview.total_if_early) })}{#if deadline} {t('reg.price.early_by', { date: when(deadline) })}{/if}</p>
					{/if}
					<p class="text-xs text-muted">{t('reg.price.note')}</p>
				{/if}
			</section>

			{#if cfg.terms_text}
				<section class="card card-body gap-3" aria-labelledby="reg-terms">
					<h2 id="reg-terms" class="font-display text-base font-bold text-ink">{t('reg.terms.title')}</h2>
					<div class="max-h-48 overflow-y-auto rounded-md border border-line bg-surface p-3 text-sm whitespace-pre-line">{cfg.terms_text}</div>
					<label class="flex items-start gap-3 text-sm"><input type="checkbox" class="checkbox mt-0.5" bind:checked={accepted} aria-invalid={err('terms') ? 'true' : undefined} /> <span>{t('reg.terms.accept')}<span class="text-error" aria-hidden="true"> *</span></span></label>
					{#if err('terms')}<span class="text-xs text-error">{err('terms')}</span>{/if}
				</section>
			{/if}

			<!-- Honeypot: real visitors never see or fill this. -->
			<div class="absolute -left-[9999px] h-0 w-0 overflow-hidden" aria-hidden="true">
				<label>Website <input type="text" name="website" tabindex="-1" autocomplete="off" bind:value={website} /></label>
			</div>

			<button type="submit" class="btn btn-primary w-full sm:w-auto sm:self-start" disabled={busy}>{busy ? t('reg.submitting') : t('reg.submit')}</button>
			<p class="text-xs text-muted">{t('reg.after_submit')}</p>
		</form>
	{/if}
</PublicShell>
