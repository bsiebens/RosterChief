<script lang="ts">
	import { CircleCheck, CircleQuestionMark, Clock, FileCheck2, FileClock, FileWarning, FileX2, Save, Upload, UserCheck, XCircle } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import PublicShell from '#lib/components/registration/PublicShell.svelte';
	import AnswerInput from '#lib/components/requirements/AnswerInput.svelte';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import type { ChecklistItem, PersonStatus, RegistrationStatus } from '#lib/registration-public.ts';
	import { answerFromForm, answerText } from '#lib/requirements.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	// svelte-ignore state_referenced_locally
	let reg = $state<RegistrationStatus>(data.status);
	$effect(() => {
		reg = data.status;
	});

	let busy = $state('');
	let errors = $state<Record<string, string>>({});
	let done = $state('');

	const day = (iso: string) => new Intl.DateTimeFormat(getLang(), { dateStyle: 'long' }).format(new Date(iso));
	const key = (p: PersonStatus, i: ChecklistItem) => `${p.member_id}:${i.requirement_id}`;

	const look: Record<ChecklistItem['display'], { icon: typeof FileCheck2; badge: string; tint: string }> = {
		missing: { icon: FileX2, badge: 'badge-warning', tint: 'text-warn-text' },
		complete: { icon: FileCheck2, badge: 'badge-success', tint: 'text-ok-text' },
		expiring_soon: { icon: FileClock, badge: 'badge-warning', tint: 'text-warn-text' },
		expired: { icon: FileWarning, badge: 'badge-error', tint: 'text-danger' },
		bypassed: { icon: UserCheck, badge: '', tint: 'text-muted' }
	};

	const errorFor = (code: string | undefined, status: number, message?: string) => {
		if (status === 429 || code === 'rate_limited') return t('reg.err.rate_limited');
		if (status === 0) return t('reg.err.network');
		if (code === 'not_a_question') return t('regs.answer.not_a_question');
		if (code === 'already_resolved' || code === 'not_required' || code === 'cancelled' || code === 'unknown_person' || code === 'unknown_requirement') return t(`regs.upload.${code}`);
		return message || t('regs.upload.failed');
	};

	async function upload(e: SubmitEvent, p: PersonStatus, item: ChecklistItem) {
		e.preventDefault();
		const form = e.currentTarget as HTMLFormElement;
		const file = new FormData(form).get('document');
		const k = key(p, item);
		errors[k] = '';
		done = '';
		if (!(file instanceof File) || !file.size) {
			errors[k] = t('regs.upload.choose');
			return;
		}
		busy = k;
		const body = new FormData();
		body.set('document', file);
		body.set('member_id', p.member_id);
		const res = await api<RegistrationStatus>('POST', `/public/registration/status/${encodeURIComponent(data.token)}/documents/${item.requirement_id}`, { form: body });
		busy = '';
		if (res.ok && res.data) {
			reg = res.data;
			done = k;
			form.reset();
			return;
		}
		errors[k] = errorFor(res.code, res.status, res.fields.document || res.message);
	}

	async function saveAnswer(e: SubmitEvent, p: PersonStatus, item: ChecklistItem) {
		e.preventDefault();
		const k = key(p, item);
		errors[k] = '';
		done = '';
		busy = k;
		const answer = answerFromForm(item.question_type, new FormData(e.currentTarget as HTMLFormElement).getAll('answer').map(String));
		const res = await api<RegistrationStatus>('POST', `/public/registration/status/${encodeURIComponent(data.token)}/answers/${item.requirement_id}`, { body: { answer, member_id: p.member_id } });
		busy = '';
		if (res.ok && res.data) {
			reg = res.data;
			done = k;
			return;
		}
		errors[k] = errorFor(res.code, res.status, res.fields.answer || res.message);
	}
	const shown = (item: ChecklistItem) => answerText(item, item.answer, t('common.yes'), t('common.no'), day);

	const headline = $derived({ awaiting: { icon: Clock, tone: 'text-warn-text' }, confirmed: { icon: CircleCheck, tone: 'text-ok-text' }, cancelled: { icon: XCircle, tone: 'text-danger' } }[reg.status]);
</script>

<svelte:head>
	<title>{pageTitle(t('regs.title'))}</title>
	<meta name="robots" content="noindex, nofollow" />
</svelte:head>

<PublicShell title={t('regs.title')} kicker={t('reg.kicker')} club={reg.club} wide>
	<section class="card card-body gap-2" aria-live="polite">
		<div class="flex items-start gap-3">
			<headline.icon size={26} class="mt-0.5 shrink-0 {headline.tone}" aria-hidden="true" />
			<div>
				<p class="font-display text-xl font-bold text-ink">{t(`regs.status.${reg.status}.title`)}</p>
				<p class="text-muted">{t(`regs.status.${reg.status}.body`)}</p>
			</div>
		</div>
		<dl class="mt-2 flex flex-wrap gap-x-8 gap-y-2 text-sm">
			<div><dt class="text-xs text-muted">{t('regs.season')}</dt><dd class="font-semibold text-ink">{reg.season_name}</dd></div>
			<div><dt class="text-xs text-muted">{t('regs.submitted')}</dt><dd class="font-semibold text-ink">{day(reg.submitted_at)}</dd></div>
			{#if reg.invoice_number}<div><dt class="text-xs text-muted">{t('regs.invoice')}</dt><dd class="font-mono font-semibold text-ink">{reg.invoice_number}</dd></div>{/if}
			{#if reg.invoice_due_date}<div><dt class="text-xs text-muted">{t('regs.due')}</dt><dd class="font-semibold text-ink">{day(reg.invoice_due_date)}</dd></div>{/if}
		</dl>
	</section>

	{#each reg.people as p (p.member_id)}
		<section class="card card-body gap-4" aria-labelledby="p-{p.member_id}">
			<div>
				<h2 id="p-{p.member_id}" class="font-display text-lg font-extrabold tracking-wide text-ink uppercase">{p.name}</h2>
				<p class="text-sm text-muted">{p.description}{#if p.requested_team} · {t('regs.team', { team: p.requested_team })}{/if} · {t(`join.kind.${p.entry_kind}`)}</p>
				{#if p.credit && Number(p.credit) > 0}<p class="text-xs text-ok-text">{p.is_credit ? t('join.price.credit_given', { amount: money(p.credit) }) : t('join.price.credit_taken', { amount: money(p.credit) })}</p>{/if}
				<p class="mt-1 text-xs"><span class="badge badge-sm {p.membership_status === 'active' ? 'badge-success' : p.membership_status === 'cancelled' ? 'badge-error' : 'badge-warning'}">{t(`regs.membership.${p.membership_status}`)}</span></p>
			</div>

			{#if p.fee}
				<div class="grid gap-2 rounded-lg border border-line p-3 text-sm">
					<div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
						<span class="text-muted">{t('regs.fee.amount')}</span><span class="font-mono font-semibold text-ink">{money(p.fee.fee_amount)}</span>
					</div>
					<div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1"><span class="text-muted">{t('regs.fee.paid')}</span><span class="font-mono">{money(p.fee.amount_paid)}</span></div>
					<div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-t border-rule pt-2">
						<span class="font-semibold text-ink">{t('regs.fee.due')}</span><span class="font-mono font-bold {Number(p.fee.remaining_balance) > 0 ? 'text-ink' : 'text-ok-text'}">{money(p.fee.remaining_balance)}</span>
					</div>
					{#if p.fee.fee_status === 'paid' || p.fee.fee_status === 'waived'}<p class="text-xs text-ok-text">{t(`regs.fee.${p.fee.fee_status}`)}</p>{/if}
					{#if p.fee.early_bird && Number(p.fee.remaining_balance) > 0}
						<p class="rounded-md px-3 py-2 text-xs {p.fee.early_bird.applies ? 'bg-ok-bg text-ok-text' : 'bg-surface text-muted'}">
							{p.fee.early_bird.applies ? t('regs.fee.early_applies', { date: day(p.fee.early_bird.deadline), discount: money(p.fee.early_bird.discount), amount: money(p.fee.early_bird.fee_if_paid_in_time) }) : t('regs.fee.early_gone', { date: day(p.fee.early_bird.deadline) })}
						</p>
					{/if}
				</div>
			{:else if reg.status === 'awaiting'}
				<p class="text-xs text-muted">{t('regs.fee.pending')}</p>
			{/if}

			<div class="grid gap-3">
				<h3 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('regs.checklist.title')}</h3>
				{#if !p.checklist.length}
					<p class="text-sm text-muted">{t('regs.checklist.none')}</p>
				{:else}
					<ul class="grid gap-3 sm:grid-cols-2">
						{#each p.checklist as item (item.requirement_id)}
							{@const l = look[item.display]}
							{@const k = key(p, item)}
							<li class="grid gap-2 rounded-lg border border-line p-3.5">
								<div class="flex items-start gap-3">
									{#if item.kind === 'question' && item.display === 'missing'}<CircleQuestionMark size={22} class="mt-0.5 shrink-0 {l.tint}" aria-hidden="true" />{:else}<l.icon size={22} class="mt-0.5 shrink-0 {l.tint}" aria-hidden="true" />{/if}
									<div class="min-w-0 flex-1">
										<p class="font-semibold text-ink">{item.name}</p>
										{#if item.description}<p class="text-xs text-muted">{item.description}</p>{/if}
										{#if item.valid_until}<p class="text-xs text-muted">{t('regs.checklist.valid_until', { date: day(item.valid_until) })}</p>{/if}
									</div>
									<span class="badge badge-sm shrink-0 {l.badge}">{t(`regs.checklist.${item.display}`)}</span>
								</div>
								{#if item.can_upload}
									<form class="grid gap-2" onsubmit={(e) => upload(e, p, item)}>
										<label class="sr-only" for="f-{k}">{t('regs.upload.label', { name: item.name })}</label>
										<input id="f-{k}" name="document" type="file" accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.heic,.txt,.doc,.docx,.odt" class="file-input file-input-bordered file-input-sm w-full" aria-invalid={errors[k] ? 'true' : undefined} />
										<div class="flex flex-wrap items-center justify-between gap-2">
											<span class="text-xs text-muted">{t('regs.upload.help')}</span>
											<button class="btn btn-primary btn-xs gap-1" type="submit" disabled={busy === k}><Upload size={13} aria-hidden="true" /> {busy === k ? t('regs.upload.sending') : t('regs.upload.send')}</button>
										</div>
										{#if errors[k]}<p class="text-xs text-error" role="alert">{errors[k]}</p>{/if}
									</form>
								{/if}
								{#if item.kind === 'question'}
									{#if item.can_answer}
										<form class="grid gap-2" onsubmit={(e) => saveAnswer(e, p, item)}>
											<label class="label-text" for="a-{k}" id="a-{k}-l">{t('regs.answer.label')}</label>
											{#key item.answer}<AnswerInput question={item} value={item.answer} id="a-{k}" labelledby="a-{k}-l" invalid={!!errors[k]} />{/key}
											<div class="flex flex-wrap items-center justify-end gap-2">
												<button class="btn btn-primary btn-xs gap-1" type="submit" disabled={busy === k}><Save size={13} aria-hidden="true" /> {busy === k ? t('regs.upload.sending') : item.display === 'missing' || item.display === 'expired' ? t('regs.answer.save') : t('regs.answer.update')}</button>
											</div>
											{#if errors[k]}<p class="text-xs text-error" role="alert">{errors[k]}</p>{/if}
										</form>
									{:else if item.answer !== null && item.answer !== undefined && item.answer !== ''}
										<p class="rounded-md bg-surface px-3 py-2 text-sm text-ink"><span class="block text-xs text-muted">{t('regs.answer.yours')}</span>{shown(item)}</p>
									{/if}
								{/if}
								{#if done === k}<p class="text-xs font-semibold text-ok-text" role="status">{item.kind === 'question' ? t('regs.answer.done') : t('regs.upload.done')}</p>{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		</section>
	{/each}

	{#if reg.status === 'cancelled'}<Alert kind="warning">{t('regs.cancelled_note')}</Alert>{/if}
	<p class="text-xs text-muted">{t('regs.keep_link')}</p>
</PublicShell>
