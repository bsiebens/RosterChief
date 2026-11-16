<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Ban, Banknote, FileDown, Mail, MailCheck, ReceiptText, Trash2, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import LegacyInvoiceHistory from '#lib/components/registration/LegacyInvoiceHistory.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import PaymentDialog from '#lib/components/finance/PaymentDialog.svelte';
	import InstallmentSchedule from '#lib/components/registration/InstallmentSchedule.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { formatDay } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { invoicePdfHref, paymentBadge, registrationHref, type InvoicePayment } from '#lib/registrations.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.item);
	const confirmed = $derived(item.status === 'confirmed');
	const lines = $derived(item.lines.filter((l) => !l.is_credit && !l.excluded));
	const open = $derived(Number(item.balance) > 0);

	let payOpen = $state(false);
	let payFor = $state<string | null>(null);
	let removing = $state<InvoicePayment | null>(null);
	let removeOpen = $state(false);
	let busy = $state(false);
	const startPay = (membershipId: string | null) => {
		payFor = membershipId;
		payOpen = true;
	};
	const run = (close?: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close?.();
		};
	};

	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const sentOk = $derived(form && 'sent' in form ? !!form.sent : true);
	const failure = $derived.by(() => {
		if (!form || 'done' in form || !('op' in form)) return '';
		const code = String(form.code ?? '');
		if (['nothing_due', 'cancelled', 'not_confirmed'].includes(code)) return t(`fin.err.${code}`);
		return String(form.message || t('common.error.generic'));
	});
	const payFailure = $derived(form && 'op' in form && form.op === 'pay' ? failure : '');
	const payFields = $derived((form && 'op' in form && form.op === 'pay' && 'fields' in form ? form.fields : {}) as Record<string, string>);
	const otherFailure = $derived(form && 'op' in form && form.op === 'pay' ? '' : failure);

	usePageHeader(() => ({ title: t('fin.detail.title'), context: item.invoice_number ?? item.contact?.name ?? '', actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/finance"><ArrowLeft size={16} /> {t('fin.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('fin.detail.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if done === 'pay'}<Alert kind="success">{t('fin.paid_ok')}</Alert>{/if}
	{#if done === 'deletePayment'}<Alert kind="success">{t('fin.deleted_ok')}</Alert>{/if}
	{#if done === 'resendInvoice'}<Alert kind={sentOk ? 'success' : 'warning'}>{sentOk ? t('fin.sent_ok') : t('fin.sent_failed')}</Alert>{/if}
	{#if otherFailure}<Alert kind="error" title={t('fin.action_failed')}>{otherFailure}</Alert>{/if}
	{#if item.early_bird}
		<Alert kind="success" title={t('fin.early.title')}>{t('fin.early.body', { date: formatDay(item.early_bird.deadline), discount: money(item.early_bird.discount), amount: money(item.early_bird.amount_due) })}</Alert>
	{/if}
	{#if item.status === 'awaiting'}<Alert kind="info">{t('fin.not_confirmed')} <a class="link font-semibold" href={registrationHref(item.id)}>{t('fin.act.review')}</a></Alert>{/if}
	{#if item.status === 'cancelled'}<Alert kind="warning">{t('fin.cancelled')}</Alert>{/if}

	{#if item.legacy_invoice_history}
		<LegacyInvoiceHistory invoice={item.legacy_invoice_history} invoiceNumber={item.invoice_number} downloadId={item.status === 'confirmed' && item.invoice_number ? item.id : null} />
	{/if}

	{#if !item.legacy_invoice_only}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<section class="card card-body card-hl gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Users size={16} aria-hidden="true" /> {t('fin.card.people')}</h2>
				<p class="text-xs text-muted">{t('fin.card.people_help')}</p>
				<div class="overflow-x-auto">
					<table class="table">
						<thead>
							<tr><th>{t('fin.person')}</th><th class="hidden text-right lg:table-cell">{t('fin.col.owed')}</th><th class="hidden text-right lg:table-cell">{t('fin.col.paid')}</th><th class="text-right">{t('fin.col.balance')}</th><th>{t('fin.col.status')}</th><th><span class="sr-only">{t('fin.col.actions')}</span></th></tr>
						</thead>
						<tbody>
							{#each lines as l (l.id)}
								<tr>
									<td>
										<a class="link font-semibold" href="/members/{l.member.id}">{l.member.name}</a>
										<p class="text-xs text-muted">{l.description}</p>
										{#if l.next_installment}
											<p class="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
												{#if l.next_installment.late}<span class="badge badge-error badge-sm">{t('installments.late')}</span>{:else}{t('installments.next_due')}{/if}
												{formatDay(l.next_installment.due_date)} · {money(l.next_installment.remaining)}
											</p>
										{/if}
									</td>
									<td class="hidden text-right font-mono text-sm lg:table-cell">{money(l.amount_due)}</td>
									<td class="hidden text-right font-mono text-sm lg:table-cell">{money(l.amount_paid)}</td>
									<td class="text-right font-mono text-sm font-semibold">{money(l.balance)}</td>
									<td><span class="badge badge-sm {l.fee_status === 'paid' ? 'badge-success' : l.fee_status === 'partially_paid' ? 'badge-info' : l.fee_status === 'waived' ? 'badge-ghost' : 'badge-neutral'}">{t(`fin.fee.${l.fee_status}`)}</span></td>
									<td class="text-right whitespace-nowrap">
										{#if confirmed && Number(l.balance) > 0}<button class="btn btn-outline btn-xs gap-1" type="button" onclick={() => startPay(l.membership_id)} aria-label={t('fin.pay_for', { name: l.member.name })}><Banknote size={13} /> {t('fin.pay_person')}</button>{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>

			{#if item.installments.length}<InstallmentSchedule rows={item.installments} />{/if}

			<section class="card card-body card-hl gap-3">
				<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Banknote size={16} aria-hidden="true" /> {t('fin.card.payments')}</h2>
				{#if !item.payments.length}
					<p class="text-sm text-muted">{t('fin.no_payments')}</p>
				{:else}
					<div class="overflow-x-auto">
						<table class="table">
							<thead>
								<tr><th>{t('fin.col.date')}</th><th>{t('fin.person')}</th><th class="text-right">{t('fin.dlg.amount')}</th><th>{t('fin.col.method')}</th><th class="hidden lg:table-cell">{t('fin.col.reference')}</th><th class="hidden xl:table-cell">{t('fin.col.by')}</th><th><span class="sr-only">{t('fin.col.actions')}</span></th></tr>
							</thead>
							<tbody>
								{#each item.payments as p (p.id)}
									<tr>
										<td class="font-mono text-xs whitespace-nowrap">{formatDay(p.paid_at)}</td>
										<td class="text-sm">{p.member.name}</td>
										<td class="text-right font-mono text-sm font-semibold">{money(p.amount)}</td>
										<td class="text-sm">{t(`fin.method.${p.method}`)}{#if p.voucher_number}<p class="font-mono text-xs text-muted">{p.voucher_number}</p>{/if}</td>
										<td class="hidden text-xs text-muted lg:table-cell">{p.reference || '–'}{#if p.note}<p>{p.note}</p>{/if}</td>
										<td class="hidden text-xs text-muted xl:table-cell">{p.recorded_by ?? '–'}</td>
										<td class="text-right">
											<button class="btn btn-outline btn-error btn-xs gap-1" type="button" onclick={() => { removing = p; removeOpen = true; }} aria-label={t('fin.delete_named', { amount: money(p.amount), name: p.member.name })}><Trash2 size={13} /> {t('fin.delete')}</button>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</section>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20" aria-label={t('fin.card.summary')}>
			<section class="card card-body gap-3">
				<div class="flex items-center justify-between gap-2">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ReceiptText size={16} aria-hidden="true" /> {t('fin.card.summary')}</h2>
					{#if item.payment_status}<span class="badge badge-sm {paymentBadge(item.payment_status)}">{t(`fin.status.${item.payment_status}`)}</span>{/if}
				</div>
				<dl class="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
					<dt class="text-muted">{t('fin.sum.invoice')}</dt><dd class="text-right font-mono text-xs">{item.invoice_number ?? '–'}</dd>
					<dt class="text-muted">{t('fin.sum.contact')}</dt><dd class="text-right">{item.contact?.name ?? '–'}</dd>
					{#if item.next_installment}
						<dt class="text-muted">{t('installments.next_due')}</dt><dd class="text-right {item.next_installment.late ? 'font-semibold text-error' : ''}">{formatDay(item.next_installment.due_date)}</dd>
					{:else if item.invoice_due_date && !item.installment_offsets.length}
						<dt class="text-muted">{t('fin.sum.due_date')}</dt><dd class="text-right {item.payment_status === 'overdue' ? 'font-semibold text-error' : ''}">{formatDay(item.invoice_due_date)}</dd>
					{/if}
					<dt class="text-muted">{t('fin.sum.amount')}</dt><dd class="text-right font-mono">{money(item.amount_due)}</dd>
					<dt class="text-muted">{t('fin.sum.paid')}</dt><dd class="text-right font-mono">{money(item.amount_paid)}</dd>
				</dl>
				<div class="flex items-baseline justify-between border-t border-rule pt-2"><span class="font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('fin.sum.balance')}</span><span class="font-display text-2xl font-extrabold text-ink tabular-nums">{money(item.balance)}</span></div>
				{#if confirmed && open}
					<button class="btn btn-primary w-full gap-2" type="button" onclick={() => startPay(null)}><Banknote size={16} /> {t('fin.pay')}</button>
				{:else if confirmed}
					<p class="text-xs font-semibold text-ok-text">{t('fin.fully_paid')}</p>
				{/if}
				<ActionList>
					{#if item.next_installment?.late && item.contact?.email}
						<ActionRow icon={Mail} href={`mailto:${encodeURIComponent(item.contact.email)}?subject=${encodeURIComponent(item.invoice_number ?? '')}`}>{t('installments.contact')}</ActionRow>
					{/if}
					{#if confirmed}<ActionRow icon={FileDown} chevron href={invoicePdfHref(item.id)} download="{item.invoice_number ?? 'invoice'}.pdf">{t('fin.act.pdf')}</ActionRow>{/if}
					{#if confirmed}<form method="POST" action="?/resendInvoice" use:enhance={run()}><ActionRow icon={MailCheck} type="submit" disabled={busy}>{t('fin.act.resend')}</ActionRow></form>{/if}
					<ActionRow icon={ReceiptText} chevron href={registrationHref(item.id)}>{t('fin.act.review')}</ActionRow>
				</ActionList>
			</section>
		</aside>
	</div>
	{/if}
</div>

<PaymentDialog bind:open={payOpen} {item} membershipId={payFor} error={payFailure} fields={payFields} />

<Modal bind:open={removeOpen} title={t('fin.delete_title')}>
	{#if removing}
		<form method="POST" action="?/deletePayment" class="grid gap-3" use:enhance={run(() => (removeOpen = false))}>
			<input type="hidden" name="payment_id" value={removing.id} />
			<p>{t('fin.delete_body', { amount: money(removing.amount), name: removing.member.name })}</p>
			{#if removing.voucher_number}<p class="text-sm text-muted">{t('fin.delete_voucher', { number: removing.voucher_number })}</p>{/if}
			<div class="modal-action mt-2 flex justify-end gap-2">
				<button class="btn btn-outline" type="button" onclick={() => (removeOpen = false)}>{t('reg.keep')}</button>
				<button class="btn btn-error gap-2" type="submit" disabled={busy}><Ban size={16} /> {t('fin.delete_confirm')}</button>
			</div>
		</form>
	{/if}
</Modal>
