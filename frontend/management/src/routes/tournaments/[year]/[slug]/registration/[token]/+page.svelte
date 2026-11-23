<script lang="ts">
	import { CircleCheck, CircleX, Clock, Save, Upload } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ClubCrest from '#lib/components/ClubCrest.svelte';
	import AddPersonButtons from '#lib/components/tournaments/AddPersonButtons.svelte';
	import PlayersEditor from '#lib/components/tournaments/PlayersEditor.svelte';
	import TeamMessages from '#lib/components/tournaments/public/TeamMessages.svelte';
	import { page } from '$app/state';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { dayLabel, draftFrom, isBlank, playerBody, type PlayerDraft, type RegistrationStatus } from '#lib/tournaments-public.ts';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let reg = $state<RegistrationStatus>(data.status);
	// svelte-ignore state_referenced_locally
	let players = $state<PlayerDraft[]>(data.status.players.map(draftFrom));
	$effect(() => {
		reg = data.status;
		players = data.status.players.map(draftFrom);
	});

	const base = $derived(`/public/tournaments/${encodeURIComponent(page.params.slug ?? '')}/registrations/${encodeURIComponent(data.token)}`);
	const r = $derived(reg.registration);
	const tn = $derived(reg.tournament);
	const submitted = $derived(page.url.searchParams.get('submitted') === '1');
	const withdrawn = $derived(!!reg.team?.withdrawn_at);
	const canLogo = $derived(r.status !== 'rejected');

	let errors = $state<Record<string, string>>({});
	let saving = $state(false);
	let uploading = $state(false);
	let saved = $state(false);
	let formError = $state('');
	let logoError = $state('');

	const failure = (res: { status: number; code?: string; message?: string }) => {
		if (res.status === 429 || res.code === 'rate_limited') return t('reg.err.rate_limited');
		if (res.status === 0) return t('reg.err.network');
		if (res.code === 'locked') return t('tp.status.locked');
		return res.message || t('reg.err.generic');
	};

	async function save(ev: SubmitEvent) {
		ev.preventDefault();
		if (saving) return;
		formError = '';
		saved = false;
		const need = t('reg.err.required');
		const local: Record<string, string> = {};
		players.forEach((p, i) => {
			if (isBlank(p)) return;
			if (!p.first_name.trim()) local[`players.${i}.first_name`] = need;
			if (!p.last_name.trim()) local[`players.${i}.last_name`] = need;
		});
		errors = local;
		if (Object.keys(local).length) return;
		saving = true;
		const res = await api<RegistrationStatus>('PUT', `${base}/players`, { body: { players: players.map(playerBody) } });
		saving = false;
		if (res.ok && res.data) {
			reg = res.data;
			players = res.data.players.map(draftFrom);
			saved = true;
			return;
		}
		if (Object.keys(res.fields).length) {
			errors = res.fields;
			formError = t('reg.err.fix');
		} else formError = failure(res);
	}

	async function uploadLogo(e: Event & { currentTarget: HTMLInputElement }) {
		const input = e.currentTarget;
		const file = input.files?.[0];
		if (!file) return;
		logoError = '';
		if (file.size > 2 * 1024 * 1024) {
			logoError = t('tp.err.logo_size');
			input.value = '';
			return;
		}
		uploading = true;
		const form = new FormData();
		form.set('logo', file);
		const res = await api<RegistrationStatus>('POST', `${base}/logo`, { form });
		uploading = false;
		input.value = '';
		if (res.ok && res.data) reg = { ...reg, registration: res.data.registration };
		else logoError = res.fields.logo || failure(res);
	}

	let tab = $state<'team' | 'messages'>(page.url.searchParams.get('tab') === 'messages' ? 'messages' : 'team');
	let unread = $derived(reg.unread_messages ?? 0);
	const payKey = $derived(reg.team ? `tp.pay.${reg.team.payment_status}` : '');
</script>

<svelte:head>
	<title>{pageTitle(`${tn.name} · ${t('tp.status.title')}`)}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

{#if submitted && r.status === 'pending'}
	<Alert kind="success" title={t('tp.status.submitted')}>{t('tp.status.submitted_body', { email: r.contact_email })}</Alert>
{/if}

<section class="card card-body gap-4" aria-labelledby="tp-st">
	<div class="flex items-center gap-3">
		<ClubCrest name={r.name} logoUrl={r.logo_url} size={56} bg={r.primary_color || undefined} />
		<div class="min-w-0 flex-1">
			<h2 id="tp-st" class="truncate font-display text-xl font-extrabold text-ink">{r.name}</h2>
			<p class="text-sm text-muted">{t('tp.status.submitted_on', { date: dayLabel(r.submitted_at) })}</p>
		</div>
		{#if withdrawn}<span class="badge badge-error">{t('tp.team.withdrawn')}</span>
		{:else if r.status === 'approved'}<span class="badge badge-success gap-1"><CircleCheck size={13} aria-hidden="true" /> {t('tp.status.approved')}</span>
		{:else if r.status === 'rejected'}<span class="badge badge-error gap-1"><CircleX size={13} aria-hidden="true" /> {t('tp.status.rejected')}</span>
		{:else}<span class="badge badge-warning gap-1"><Clock size={13} aria-hidden="true" /> {t('tp.status.pending')}</span>{/if}
	</div>

	{#if withdrawn}
		<Alert kind="error" title={t('tp.team.withdrawn')}>{reg.team?.withdrawal_reason || t('tp.status.withdrawn_body')}</Alert>
	{:else if r.status === 'pending'}
		<p class="text-sm text-muted">{t('tp.status.pending_body')}</p>
	{:else if r.status === 'approved'}
		<Alert kind="success" title={t('tp.status.approved_title')}>{t('tp.status.approved_body')}</Alert>
		{#if reg.team}<a class="link text-sm" href="/tournaments/{tn.path}/teams/{reg.team.slug}">{t('tp.status.team_page')} →</a>{/if}
	{:else}
		<Alert kind="error" title={t('tp.status.rejected_title')}>{r.rejection_reason || t('tp.status.rejected_body')}</Alert>
	{/if}

	{#if canLogo}
		<div class="form-control">
			<label class="label" for="st-logo"><span class="label-text inline-flex items-center gap-1"><Upload size={13} aria-hidden="true" /> {r.logo_url ? t('tp.status.logo_change') : t('tp.f.logo')}</span></label>
			<input id="st-logo" type="file" accept="image/png,image/jpeg,image/gif,image/webp" class="file-input file-input-bordered w-full max-w-md" onchange={uploadLogo} disabled={uploading} />
			<span class="mt-1 block text-xs {logoError ? 'text-error' : 'text-muted'}" role={logoError ? 'alert' : undefined}>{logoError || (uploading ? t('tp.status.uploading') : t('tp.f.logo_help'))}</span>
		</div>
	{/if}
</section>

<div class="flex gap-2" role="tablist">
	{#each [['team', t('ts.team.tab_team')], ['messages', t('ts.team.tab_messages')]] as [key, label] (key)}
		<button type="button" role="tab" aria-selected={tab === key} class="flex items-center gap-2 rounded-full border px-4 py-1.5 font-display text-xs font-extrabold tracking-[.08em] uppercase {tab === key ? 'border-ink bg-ink text-white' : 'border-line bg-white text-muted hover:text-ink'}" onclick={() => (tab = key as 'team' | 'messages')}>
			{label}{#if key === 'messages' && unread > 0 && tab !== 'messages'}<span class="rounded-full bg-danger px-1.5 text-[10px] text-white">{unread}</span>{/if}
		</button>
	{/each}
</div>

{#if tab === 'messages'}
	<TeamMessages {base} onread={() => (reg = { ...reg, unread_messages: 0 })} />
{:else}
{#if reg.team && r.status === 'approved' && !withdrawn}
	<section class="card card-body gap-2" aria-labelledby="tp-pay">
		<h2 id="tp-pay" class="font-display text-base font-bold text-ink">{t('tp.status.payment')}</h2>
		<dl class="grid gap-1 text-sm">
			<div class="flex justify-between gap-4"><dt class="text-muted">{t('tp.status.pay_state')}</dt><dd class="font-semibold">{t(payKey)}</dd></div>
			{#if tn.lunch_mode !== 'none' && reg.lunch_total !== null}
				<div class="flex justify-between gap-4"><dt class="text-muted">{t('tp.status.lunch_total')}</dt><dd>{money(reg.lunch_total)}</dd></div>
			{/if}
			{#if reg.amount_owed !== null}
				<div class="flex justify-between gap-4 border-t border-rule pt-2"><dt class="font-semibold">{t('tp.status.amount_owed')}</dt><dd class="font-display text-lg font-extrabold">{money(reg.amount_owed)}</dd></div>
			{/if}
		</dl>
		<p class="text-xs text-muted">{t('tp.status.pay_note', { email: tn.contact_email })}</p>
	</section>
{/if}

{#if r.status !== 'rejected' && !withdrawn}
	<section class="card card-body gap-3" aria-labelledby="tp-players">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<h2 id="tp-players" class="font-display text-base font-bold text-ink">{t('tp.register.roster')}</h2>
			{#if reg.players_editable}<AddPersonButtons bind:players dates={tn.dates} mandatory={tn.lunch_mode === 'mandatory'} />{/if}
		</div>
		{#if reg.players_editable}
			<form class="grid gap-3" onsubmit={save} novalidate>
				<p class="text-sm text-muted">{r.status === 'pending' ? t('tp.status.edit_pending') : t('tp.status.edit_approved', { date: dayLabel(tn.start_date) })}</p>
				{#if formError || Object.keys(errors).length}<Alert kind="error" title={formError || t('reg.err.fix')}>{#each Object.entries(errors).filter(([, m]) => m) as [k, m] (k)}<p>{m}</p>{/each}</Alert>{/if}
				{#if saved}<Alert kind="success">{t('tp.status.saved')}</Alert>{/if}
				<PlayersEditor bind:players tournament={tn} {errors} />
				<button type="submit" class="btn btn-primary gap-2 self-start" disabled={saving}><Save size={16} aria-hidden="true" /> {saving ? t('reg.submitting') : t('tp.status.save')}</button>
			</form>
		{:else}
			{#if reg.roster_closed}<p class="text-sm text-muted">{t('tp.status.roster_closed')}</p>{/if}
			{#if reg.players.length}
				<div class="overflow-x-auto">
					<table class="table">
						<thead><tr><th class="w-12">#</th><th>{t('tp.col.name')}</th><th>{t('tp.col.position')}</th></tr></thead>
						<tbody>
							{#each reg.players as p, i (i)}
								<tr><td class="font-mono">{p.jersey_number ?? ''}</td><td>{p.first_name} {p.last_name}{#if p.age_flagged} <span class="badge badge-warning badge-sm ml-1">{t('tp.age.flag_short')}</span>{/if}</td><td class="text-muted">{p.position_label || (p.role === 'staff' ? t('tp.role.staff') : '')}</td></tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		{/if}
	</section>
{/if}

{/if}

<p class="text-xs text-muted">{t('tp.status.keep_link')}{#if tn.contact_email}{' '}<a class="link" href="mailto:{tn.contact_email}">{tn.contact_email}</a>{/if}</p>
