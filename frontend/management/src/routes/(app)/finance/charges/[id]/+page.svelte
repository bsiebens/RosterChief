<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { ArrowLeft, BellRing, ClipboardList, Download, ListOrdered, Save, Send, Settings2, Trash2, XCircle } from '@lucide/svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ChargeForm, { lineTotal, newLine, type LineDraft } from '#lib/components/finance/ChargeForm.svelte';
	import ChargePayments from '#lib/components/finance/ChargePayments.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { chargePdfHref, dateTime, day, money, statusBadge } from '#lib/charges.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const c = $derived(data.charge);

	// The draft's editable state starts from what was saved and is reset when the saved invoice changes (after a save).
	let memberId = $state('');
	let lines = $state<LineDraft[]>([]);
	let dueDate = $state('');
	let notes = $state('');
	let invoiceText = $state('');
	let paymentInstructions = $state('');
	$effect(() => {
		const saved = data.charge;
		untrack(() => {
			memberId = saved.member.id;
			lines = saved.lines.map((l) => newLine({ description: l.description, quantity: l.quantity, unit_price: l.unit_price }));
			dueDate = saved.due_date ?? '';
			notes = saved.notes;
			invoiceText = saved.invoice_text;
			paymentInstructions = saved.payment_instructions;
		});
	});

	let busy = $state(false);
	let sendOpen = $state(false);
	let deleteOpen = $state(false);
	let cancelOpen = $state(false);
	let remindOpen = $state(false);

	const op = $derived(form && 'op' in form ? String(form.op) : '');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const failure = $derived(op && form && !('done' in form) && !['addPayment', 'deletePayment'].includes(op) ? String(form.message || t('common.error.generic')) : '');
	const errors = $derived<Record<string, string>>(op === 'save' && form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const payload = $derived(
		JSON.stringify({
			member_id: memberId,
			lines: lines.map((l) => ({ description: l.description.trim(), quantity: Number(l.quantity) || 0, unit_price: String(l.unit_price) })),
			due_date: dueDate || null,
			notes,
			invoice_text: invoiceText,
			payment_instructions: paymentInstructions
		})
	);
	const complete = $derived(!!memberId && lines.length > 0 && lines.every((l) => l.description.trim() && lineTotal(l) > 0));
	const reminderLine = $derived(c.reminders_sent === 0 ? t('chg.d.reminders_none') : c.reminders_sent === 1 ? t('chg.d.reminders_one', { date: day(c.last_reminder_at) }) : t('chg.d.reminders', { n: c.reminders_sent, date: day(c.last_reminder_at) }));

	const run = (close: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type !== 'redirect') close();
		};
	};

	usePageHeader(() => ({ title: data.charge.number ?? t('chg.d.draft_title'), context: data.charge.member.name, badge, actions }));
</script>

{#snippet badge()}<span class="badge badge-sm {statusBadge(c.status)}">{t(`chg.status.${c.status}`)}</span>{/snippet}

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/finance/invoices"><ArrowLeft size={16} /> {t('chg.d.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(c.number ?? t('chg.d.draft_title'))}</title></svelte:head>

<div class="grid gap-5">
	{#if data.created && done === ''}<Alert kind="success">{t('chg.new.created')}</Alert>{/if}
	{#if done === 'send'}<Alert kind="success">{t('chg.d.done.sent')}</Alert>{/if}
	{#if done === 'remind'}<Alert kind="success">{t('chg.d.done.reminded')}</Alert>{/if}
	{#if done === 'cancel'}<Alert kind="success">{t('chg.d.done.cancelled')}</Alert>{/if}
	{#if done === 'save'}<Alert kind="success">{t('chg.d.done.saved')}</Alert>{/if}
	{#if failure}<Alert kind="error" title={t('chg.d.failed')}>{failure}</Alert>{/if}

	{#if c.is_draft}
		<form
			method="POST"
			action="?/save"
			class="grid gap-5"
			novalidate
			use:enhance={({ cancel }) => {
				if (!complete) {
					cancel();
					return;
				}
				busy = true;
				return async ({ update }) => {
					await update({ reset: false });
					busy = false;
				};
			}}>
			<input type="hidden" name="payload" value={payload} />
			<div class="grid items-start gap-5 xl:grid-cols-3">
				<div class="xl:col-span-2">
					<ChargeForm bind:memberId bind:lines bind:dueDate bind:notes bind:invoiceText bind:paymentInstructions defaultInstructions={c.default_payment_instructions} memberName={c.member.name} {errors} />
				</div>
				<aside class="grid content-start gap-5 xl:sticky xl:top-20">
					<section class="card card-body card-hl gap-3">
						<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ClipboardList size={16} aria-hidden="true" /> {t('chg.new.summary')}</h2>
						<dl class="grid gap-1.5 text-sm">
							{#each lines.filter((l) => l.description.trim() || Number(l.unit_price) > 0) as l (l.key)}
								<div class="flex justify-between gap-3"><dt><span class="font-mono text-muted">{Number(l.quantity) || 0} ×</span> {l.description.trim() || '…'}</dt><dd class="shrink-0 font-mono">{money(lineTotal(l))}</dd></div>
							{/each}
							<div class="flex justify-between gap-3 border-t border-line pt-1 text-base font-semibold"><dt>{t('chg.form.total')}</dt><dd class="font-mono">{money(lines.reduce((sum, l) => sum + lineTotal(l), 0))}</dd></div>
						</dl>
						<p class="text-xs text-muted">{dueDate ? t('chg.new.summary_due', { date: day(dueDate) }) : t('chg.new.summary_due_default')}. {t('chg.form.no_vat')}</p>
						<div class="grid gap-2">
							<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('chg.form.save')}</button>
							<button class="btn btn-outline w-full gap-2" type="button" disabled={!complete} onclick={() => (sendOpen = true)}><Send size={16} /> {t('chg.d.send')}</button>
							<button class="btn btn-outline btn-error w-full gap-2" type="button" onclick={() => (deleteOpen = true)}><Trash2 size={16} /> {t('chg.d.delete')}</button>
						</div>
					</section>
				</aside>
			</div>
		</form>
	{:else}
		<div class="grid items-start gap-5 xl:grid-cols-3">
			<div class="grid content-start gap-5 xl:col-span-2">
				<section class="card card-body card-hl gap-4">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ListOrdered size={16} aria-hidden="true" /> {t('chg.d.lines')}</h2>
					<dl class="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
						<div><dt class="text-muted">{t('chg.col.member')}</dt><dd class="font-semibold text-ink">{c.member.name}</dd></div>
						<div><dt class="text-muted">{t('chg.d.issued')}</dt><dd class="font-mono text-ink">{day(c.issued_at)}</dd></div>
						<div><dt class="text-muted">{t('chg.d.due')}</dt><dd class="font-mono text-ink {c.status === 'overdue' ? 'font-semibold text-danger' : ''}">{day(c.due_date)}</dd></div>
					</dl>
					<div class="overflow-x-auto">
						<table class="table">
							<thead><tr><th>{t('chg.form.description')}</th><th class="text-right">{t('chg.form.qty')}</th><th class="text-right">{t('chg.form.unit')}</th><th class="text-right">{t('chg.form.line_total')}</th></tr></thead>
							<tbody>
								{#each c.lines as l (l.id)}
									<tr><td>{l.description}</td><td class="text-right font-mono text-sm">{l.quantity}</td><td class="text-right font-mono text-sm">{money(l.unit_price)}</td><td class="text-right font-mono text-sm">{money(l.line_total)}</td></tr>
								{/each}
								<tr class="font-semibold"><td colspan="3">{t('chg.form.total')}</td><td class="text-right font-mono">{money(c.total)}</td></tr>
							</tbody>
						</table>
					</div>
					{#if c.invoice_text}<div><h3 class="text-xs font-semibold tracking-wide text-muted uppercase">{t('chg.d.invoice_text')}</h3><p class="mt-1 text-sm whitespace-pre-line">{c.invoice_text}</p></div>{/if}
					{#if c.payment_instructions || c.default_payment_instructions}<div><h3 class="text-xs font-semibold tracking-wide text-muted uppercase">{t('chg.d.payment_instructions')}</h3><p class="mt-1 text-sm whitespace-pre-line">{c.payment_instructions || c.default_payment_instructions}</p></div>{/if}
					{#if c.notes}<div><h3 class="text-xs font-semibold tracking-wide text-muted uppercase">{t('chg.d.notes')}</h3><p class="mt-1 text-sm whitespace-pre-line">{c.notes}</p></div>{/if}
					<p class="text-xs text-muted">{t('chg.d.fixed')}</p>
				</section>
				{#if c.status !== 'cancelled'}<ChargePayments charge={c} {form} />{/if}
			</div>
			<aside class="grid content-start gap-5 xl:sticky xl:top-20">
				<section class="card card-body card-hl gap-3">
					<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><Settings2 size={16} aria-hidden="true" /> {t('chg.d.actions')}</h2>
					<dl class="grid gap-1.5 text-sm">
						<div class="flex justify-between gap-3"><dt class="text-muted">{t('chg.pay.total')}</dt><dd class="font-mono">{money(c.total)}</dd></div>
						<div class="flex justify-between gap-3"><dt class="text-muted">{t('chg.pay.paid')}</dt><dd class="font-mono">{money(c.amount_paid)}</dd></div>
					</dl>
					<div class="flex items-baseline justify-between border-t border-rule pt-2"><span class="font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('chg.pay.due')}</span><span class="font-display text-2xl font-extrabold {Number(c.amount_due) > 0 && c.status !== 'cancelled' ? 'text-warn-text' : 'text-ink'}">{c.status === 'cancelled' ? '—' : money(c.amount_due)}</span></div>
					{#if c.status === 'paid'}<p class="text-xs font-semibold text-ok-text">{t('chg.status.paid')}</p>{/if}
					<ActionList>
						{#if c.number && c.status !== 'cancelled'}<ActionRow icon={Download} href={chargePdfHref(c.id)} download="{c.number}.pdf">{t('chg.d.pdf')}</ActionRow>{/if}
						{#if c.can_remind}<ActionRow icon={BellRing} onclick={() => (remindOpen = true)}>{t('chg.d.remind')}</ActionRow>{/if}
						{#if c.status !== 'cancelled' && c.status !== 'paid'}<ActionRow icon={XCircle} danger disabled={!c.can_cancel} title={c.can_cancel ? '' : t('chg.d.cancel_blocked')} onclick={() => (cancelOpen = true)}>{t('chg.d.cancel')}</ActionRow>{/if}
					</ActionList>
					{#if c.status !== 'cancelled'}<p class="text-xs text-muted">{reminderLine}</p>{/if}
				</section>
			</aside>
		</div>
	{/if}
</div>

<Modal bind:open={sendOpen} title={t('chg.d.send_title')}>
	<form method="POST" action="?/send" class="grid gap-3" use:enhance={run(() => (sendOpen = false))}>
		<p>{t('chg.d.send_body', { name: c.member.name, total: money(c.total) })}</p>
		<p class="text-sm text-muted">{c.recipient_email ? t('chg.d.send_email', { email: c.recipient_email }) : t('chg.d.send_no_email')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (sendOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Send size={16} /> {t('chg.d.send')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={deleteOpen} title={t('chg.d.delete_title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={run(() => (deleteOpen = false))}>
		<p>{t('chg.d.delete_body')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('chg.d.delete')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={cancelOpen} title={t('chg.d.cancel_title')}>
	<form method="POST" action="?/cancel" class="grid gap-3" use:enhance={run(() => (cancelOpen = false))}>
		<p>{t('chg.d.cancel_body')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (cancelOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><XCircle size={16} /> {t('chg.d.cancel')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={remindOpen} title={t('chg.d.remind_title')}>
	<form method="POST" action="?/remind" class="grid gap-3" use:enhance={run(() => (remindOpen = false))}>
		<p>{t('chg.d.remind_body')}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (remindOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><BellRing size={16} /> {t('chg.d.remind')}</button>
		</div>
	</form>
</Modal>
