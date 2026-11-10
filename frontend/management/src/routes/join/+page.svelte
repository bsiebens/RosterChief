<script lang="ts">
	import { goto } from '$app/navigation';
	import { CircleAlert, Lock, Plus, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import JerseyNumberField from '#lib/components/registration/JerseyNumberField.svelte';
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
		if (e.variant_id && !options.some((v) => v.option_id === e.variant_id)) e.variant_id = '';
		if (!e.variant_id && options.length === 1) e.variant_id = options[0].option_id;
		// An option with a team fixes it (players only): the registrant sees it but can't change it. Leaving such an option gives the choice back.
		const fixed = e.kind === 'player' ? (options.find((v) => v.option_id === e.variant_id)?.team_id ?? null) : null;
		if (fixed) {
			e.requested_team_id = fixed;
			e.team_fixed = true;
		} else if (e.team_fixed) {
			e.requested_team_id = '';
			e.team_fixed = false;
		}
	}
	const teamName = (id: string) => cfg.teams.find((x) => x.id === id)?.name ?? variantsFor('player').find((v) => v.team_id === id)?.team_name ?? '';
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

	// Live price of the people that are complete (they have chosen what to register for). Adding a person who is not complete yet changes nothing: the
	// price box keeps showing the others. While a new price loads the old one stays, dimmed, so it never looks final.
	const completeEntries = $derived(entries.filter((e) => e.variant_id));
	const previewKey = $derived(JSON.stringify(completeEntries.map(entryBody)));
	let previewUids = $state<number[]>([]);
	let previewLoading = $state(false);
	let lastKey = '';
	let seq = 0;
	$effect(() => {
		const key = previewKey;
		const uids = completeEntries.map((e) => e.uid);
		if (!uids.length) {
			seq++;
			preview = null;
			previewError = '';
			previewLoading = false;
			lastKey = '';
			return;
		}
		if (key === lastKey) {
			seq++; // back to what is shown: drop a pending request
			previewLoading = false;
			return;
		}
		previewLoading = true;
		const mine = ++seq;
		const timer = setTimeout(async () => {
			const res = await api<Preview>('POST', '/public/registration/preview', { body: { entries: JSON.parse(key) } });
			if (mine !== seq) return;
			previewLoading = false;
			previewError = res.ok ? '' : res.status === 429 ? t('reg.err.rate_limited') : t('reg.err.preview');
			preview = res.ok ? (res.data ?? null) : null;
			previewUids = uids;
			lastKey = res.ok ? key : '';
		}, 400);
		return () => clearTimeout(timer);
	});
	const incomplete = $derived(entries.length - completeEntries.length);
	/** The person a preview line is for (lines follow the complete entries of the request, not the form). */
	const lineEntry = (index: number) => entries.find((e) => e.uid === previewUids[index]);

	function addPerson() {
		if (entries.length >= cfg.max_entries) return;
		entries = [...entries, fresh()];
	}
	const removePerson = (uid: number) => (entries = entries.filter((e) => e.uid !== uid));
	// Only one person can be "myself"; a second one is a child.
	const hasSelf = (uid: number) => entries.some((e) => e.who === 'self' && e.uid !== uid);

	/** The numbers the other people in this form picked on the same team (they are not free for this one). */
	const othersOn = (e: EntryDraft) =>
		entries
			.filter((o) => o.uid !== e.uid && o.kind === 'player' && e.requested_team_id && o.requested_team_id === e.requested_team_id && o.requested_jersey_number.trim() !== '')
			.map((o) => Number(o.requested_jersey_number))
			.filter((n) => Number.isInteger(n));

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
	<title>{pageTitle(t('join.title'))}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<PublicShell title={t('join.title')} kicker={t('reg.kicker')} club={cfg.club} wide>
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
						<legend class="label-text mb-1">{t('join.kind.label')}</legend>
						<div class="flex flex-wrap gap-x-5 gap-y-2 text-sm">
							<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="{fid}-kind" value="player" bind:group={e.kind} onchange={() => reconcile(e)} /> {t('join.kind.player')}</label>
							<label class="flex items-center gap-2"><input type="radio" class="radio radio-sm" name="{fid}-kind" value="volunteer" bind:group={e.kind} onchange={() => reconcile(e)} /> {t('join.kind.volunteer')}</label>
						</div>
					</fieldset>

					<div class="form-control">
						<label class="label" for="{fid}-offer"><span class="label-text">{t('reg.f.offer')}<span class="text-error" aria-hidden="true"> *</span></span></label>
						{#if variantsFor(e.kind).length}
							<select id="{fid}-offer" class="select select-bordered w-full {eerr(i, 'variant_id') ? 'select-error' : ''}" bind:value={e.variant_id} onchange={() => reconcile(e)} aria-invalid={eerr(i, 'variant_id') ? 'true' : undefined}>
								<option value="">{t('reg.f.offer_pick')}</option>
								{#each groupedFor(e.kind) as g (g.product.id)}
									{#if g.variants.length === 1 && g.variants[0].name === g.product.name}
										<option value={g.variants[0].option_id}>{g.product.name} · {money(g.variants[0].price)}</option>
									{:else}
										<optgroup label={g.product.name}>
											{#each g.variants as v (v.option_id)}<option value={v.option_id}>{v.name} · {money(v.price)}</option>{/each}
										</optgroup>
									{/if}
								{/each}
							</select>
						{:else}
							<p id="{fid}-offer" class="rounded-lg border border-dashed border-line px-3 py-2 text-sm text-muted" role="status">{t(e.kind === 'volunteer' ? 'reg.f.offer_none_volunteer' : 'reg.f.offer_none_player')}</p>
						{/if}
						{#if eerr(i, 'variant_id')}<span class="mt-1 block text-xs text-error">{eerr(i, 'variant_id')}</span>{/if}
					</div>

					{#if e.kind === 'player'}
						<div class="grid gap-4 sm:grid-cols-3">
							{#if e.team_fixed}
								<div class="form-control">
									<span class="label"><span class="label-text">{t('reg.f.team')}</span></span>
									<p id="{fid}-team" class="flex min-h-12 items-center gap-2 rounded-lg border border-line bg-surface px-3 text-sm font-semibold text-ink"><Lock size={14} class="shrink-0 text-muted" aria-hidden="true" /> {teamName(e.requested_team_id)}</p>
									<span class="label-text-alt mt-1 block">{t('join.team.fixed_help')}</span>
								</div>
							{:else}
								<div class="form-control">
									<label class="label" for="{fid}-team"><span class="label-text">{t('reg.f.team')}</span></label>
									<select id="{fid}-team" class="select select-bordered w-full" bind:value={e.requested_team_id}>
										<option value="">{t('reg.f.no_preference')}</option>
										{#each cfg.teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
									</select>
								</div>
							{/if}
							<div class="form-control">
								<label class="label" for="{fid}-pos"><span class="label-text">{t('reg.f.position')}</span></label>
								<select id="{fid}-pos" class="select select-bordered w-full" bind:value={e.requested_position_id}>
									<option value="">{t('reg.f.no_preference')}</option>
									{#each cfg.positions as pos (pos.id)}<option value={pos.id}>{pos.name}</option>{/each}
								</select>
							</div>
							<JerseyNumberField id="{fid}-jersey" teamId={e.requested_team_id} dob={e.date_of_birth} variantId={e.variant_id} exclude={othersOn(e)} bind:value={e.requested_jersey_number} error={eerr(i, 'requested_jersey_number')} />
						</div>
						<p class="-mt-2 text-xs text-muted">{t('reg.f.team_help')}</p>
					{/if}

					<label class="flex items-start gap-3 text-sm"><input type="checkbox" class="checkbox mt-0.5" bind:checked={e.photo_public_consent} /> <span>{t('reg.f.photo_consent')}<span class="block text-xs text-muted">{t('reg.f.photo_consent_help')}</span></span></label>
				</section>
			{/each}

			{#if entries.length < cfg.max_entries}
				<button type="button" class="btn btn-outline w-full gap-2 sm:w-auto sm:self-start" onclick={addPerson}><Plus size={16} aria-hidden="true" /> {t('reg.person.add')}</button>
			{/if}

			<section class="card card-body gap-3" aria-labelledby="reg-price" aria-live="polite" aria-busy={previewLoading}>
				<div class="flex items-baseline justify-between gap-3">
					<h2 id="reg-price" class="font-display text-base font-bold text-ink">{t('reg.price.title')}</h2>
					{#if previewLoading}<span class="text-xs text-muted">{t('join.price.updating')}</span>{/if}
				</div>
				{#if previewError}
					<p class="flex items-center gap-2 text-sm text-error"><CircleAlert size={16} aria-hidden="true" /> {previewError}</p>
				{:else if !preview}
					<p class="text-sm text-muted">{t('reg.price.pick')}</p>
				{:else}
					{#if incomplete > 0}<p class="text-xs text-muted">{t('join.price.partial', { n: completeEntries.length, m: entries.length })}</p>{/if}
					<div class="grid gap-3 transition-opacity {previewLoading ? 'opacity-50' : ''}">
					<ul class="divide-y divide-rule text-sm">
						{#each preview.lines as l (l.index)}
							<li class="flex flex-wrap items-baseline justify-between gap-x-4 py-2">
								<span><span class="font-semibold text-ink">{lineEntry(l.index)?.who === 'self' ? `${contact.first_name} ${contact.last_name}`.trim() || t('reg.who.self') : `${lineEntry(l.index)?.first_name ?? ''} ${lineEntry(l.index)?.last_name ?? ''}`.trim() || t('reg.person.n', { n: l.index + 1 })}</span> <span class="text-muted">· {l.description}</span></span>
								{#if l.is_credit}
									<span class="font-mono text-ok-text"><span class="badge badge-sm badge-success mr-2">{t('join.price.credit')}</span>{money(l.base_price)}</span>
									<span class="w-full text-xs text-muted">{t('join.price.credit_help')}</span>
								{:else}
									<span class="font-mono">
										{#if Number(l.discount) > 0 || Number(l.credit_share) > 0}<span class="mr-2 text-muted line-through">{money(Number(l.base_price))}</span>{/if}{money(l.price)}
									</span>
									{#if Number(l.discount) > 0}<span class="w-full text-xs text-ok-text">{t('reg.price.discount', { amount: money(l.discount) })}</span>{/if}
									{#if Number(l.credit_share) > 0}<span class="w-full text-xs text-ok-text">{t('join.price.credit_taken', { amount: money(l.credit_share) })}</span>{/if}
								{/if}
							</li>
						{/each}
					</ul>
					<div class="flex items-baseline justify-between gap-4 border-t border-rule pt-3">
						<span class="font-display text-sm font-bold tracking-[.08em] text-ink uppercase">{t('reg.price.total')}</span>
						<span class="font-display text-2xl font-extrabold text-ink">{money(preview.total)}</span>
					</div>
					{#if Number(preview.credit_total) > Number(preview.credit_applied)}
						<p class="text-xs text-muted">{t('join.price.credit_unused', { amount: money(Number(preview.credit_total) - Number(preview.credit_applied)) })}</p>
					{/if}
					{#if Number(preview.early_bird_discount) > 0}
						{@const deadline = preview.lines.find((l) => l.early_bird_deadline)?.early_bird_deadline}
						<p class="rounded-md bg-ok-bg px-4 py-3 text-sm leading-relaxed text-ok-text"><span>{t('reg.price.early', { amount: money(preview.early_bird_discount), total: money(preview.total_if_early) })}</span>{#if deadline}<span class="ml-1.5 font-semibold">{t('reg.price.early_by', { date: when(deadline) })}</span>{/if}</p>
					{/if}
					</div>
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
