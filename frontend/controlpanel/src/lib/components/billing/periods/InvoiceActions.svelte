<script lang="ts">
	import { Download, FileX2, Mail, MailCheck, FilePlus } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { Due, VoidReissueResult } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import IssueInvoiceDialog from './IssueInvoiceDialog.svelte';
	import VoidReissueDialog from './VoidReissueDialog.svelte';
	import { dateTime, day, money2, vatPct } from './format.ts';

	let { due, canIssue, ondone }: { due: Due; canIssue: boolean; ondone: () => void } = $props();

	const inv = $derived(due.invoice);
	let dialog = $state<'' | 'send' | 'mark' | 'issue' | 'void'>('');
	let pdfBusy = $state('');
	let error = $state('');
	let result = $state<VoidReissueResult | null>(null);

	// Why the void action is not available (superusers only see it); empty when it is.
	const voidBlock = $derived(
		!inv || inv.status === 'voided' ? 'This period has no active invoice to void.'
		: due.status === 'cancelled' ? 'This period was cancelled; its invoice cannot be voided.'
		: due.credit_notes?.length ? 'This invoice has credit notes; it cannot be voided.'
		: ''
	);

	function voided(r: VoidReissueResult) {
		result = r;
		ondone();
	}

	// `number` fetches a specific (voided) invoice of the period; without it the current invoice.
	async function openPdf(number?: string) {
		pdfBusy = number ?? inv?.number ?? '';
		error = '';
		try {
			const res = await fetch(`/api/platform/v1/billing/dues/${due.id}/invoice.pdf${number ? `?number=${encodeURIComponent(number)}` : ''}`);
			if (!res.ok) {
				const json = await res.json().catch(() => null);
				error = json?.error?.message ?? 'The PDF could not be created.';
				return;
			}
			const url = URL.createObjectURL(await res.blob());
			if (!window.open(url, '_blank')) {
				// Pop-up blocked: fall back to a download.
				const a = document.createElement('a');
				a.href = url;
				a.download = `${number ?? inv?.number ?? 'invoice'}.pdf`;
				a.click();
			}
			setTimeout(() => URL.revokeObjectURL(url), 60_000);
		} catch {
			error = 'Could not reach the server. Check your connection and try again.';
		} finally {
			pdfBusy = '';
		}
	}

	const call = (path: string, fail: string) => async () => {
		const r = await platform('POST', `/billing/dues/${due.id}/invoice/${path}`);
		if (!r.ok) return r.message ?? fail;
		ondone();
	};
</script>

<div class="flex flex-col gap-2">
	<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">invoice</div>
	{#if error}<Alert kind="error" title="PDF not available">{error}</Alert>{/if}
	{#if result}
		<Alert kind="success" title="Invoice voided and reissued">
			<span class="font-mono">{result.voided.number}</span> voided → <a class="link font-mono" href="/billing?status=all&q={encodeURIComponent(result.invoice.number)}">{result.invoice.number}</a> issued{#if result.credit_note}
				· credit note <a class="link font-mono" href="/billing/credit-notes?q={encodeURIComponent(result.credit_note.number)}">{result.credit_note.number}</a>{/if}.
			{#if result.invoice.needs_sending}Send the replacement to the club again.{/if}
		</Alert>
	{/if}

	{#if inv}
		<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
			<dt class="text-muted">Number</dt>
			<dd class="font-mono">{inv.number}{#if inv.replaces_number}<span class="ml-1.5 font-sans text-xs text-muted">replaces {inv.replaces_number}</span>{/if}{#if inv.custom_number}<span class="badge badge-ghost badge-xs ml-1.5" title="Issued outside the automatic sequence">custom</span>{/if}</dd>
			<dt class="text-muted">Issued</dt>
			<dd>{day(inv.issued_at)} · due {day(inv.due_date)}</dd>
			<dt class="text-muted">Amount</dt>
			<dd class="font-mono tabular-nums">{money2(inv.net)} + {money2(inv.vat)} VAT{inv.reverse_charge ? ' (reverse charge)' : ` (${vatPct(inv.vat_rate)})`} = {money2(inv.total)}</dd>
			<dt class="text-muted">Sent</dt>
			<dd>
				{#if inv.sent_at}{dateTime(inv.sent_at)} {inv.sent_method === 'manual' ? '(marked as sent, outside the system)' : '(emailed to the club administrators)'}
				{:else if inv.needs_sending}<span class="text-club-dark">Not sent yet</span>
				{:else}Nothing to send (no amount due){/if}
			</dd>
		</dl>
		<div class="flex flex-wrap gap-2 pt-1">
			<button class="btn btn-outline btn-sm" onclick={() => openPdf()} disabled={!!pdfBusy}><Download size={13} aria-hidden="true" /> {pdfBusy === inv.number ? 'Preparing…' : 'Download PDF'}</button>
			<button class="btn btn-outline btn-sm" onclick={() => (dialog = 'send')}><Mail size={13} aria-hidden="true" /> {inv.sent_at ? 'Send again' : 'Send by email'}</button>
			{#if !inv.sent_at}
				<button class="btn btn-outline btn-sm" onclick={() => (dialog = 'mark')}><MailCheck size={13} aria-hidden="true" /> Mark as sent</button>
			{/if}
			{#if canIssue}
				<button class="btn btn-outline btn-error btn-sm" disabled={!!voidBlock || !!result} title={voidBlock || 'Void this invoice and issue a replacement'} onclick={() => (dialog = 'void')}><FileX2 size={13} aria-hidden="true" /> Void &amp; reissue</button>
			{/if}
		</div>
	{:else}
		<p class="text-sm text-muted">No invoice has been issued for this period yet.</p>
		{#if canIssue}
			<div><button class="btn btn-outline btn-sm" onclick={() => (dialog = 'issue')}><FilePlus size={13} aria-hidden="true" /> Issue invoice</button></div>
		{/if}
	{/if}

	{#if due.voided_invoices?.length}
		<div class="mt-3 flex flex-col gap-1.5">
			<div class="font-mono text-[10px] tracking-[.08em] text-muted uppercase">voided invoices</div>
			<ul class="divide-y divide-rule rounded border border-rule bg-white text-sm">
				{#each due.voided_invoices as v (v.number)}
					<li class="flex flex-col gap-0.5 px-3 py-2">
						<div class="flex flex-wrap items-center gap-2">
							<span class="badge badge-error badge-xs">void</span>
							<span class="font-mono">{v.number}</span>
							<button class="btn btn-ghost btn-xs" onclick={() => openPdf(v.number)} disabled={!!pdfBusy} aria-label="Download PDF of {v.number}"><Download size={12} aria-hidden="true" /> {pdfBusy === v.number ? 'Preparing…' : 'PDF'}</button>
							<span class="text-xs text-muted">{dateTime(v.voided_at)}</span>
						</div>
						<div class="text-xs">{v.void_reason}</div>
						<div class="text-xs text-muted">
							{#if v.replaced_by_number}replaced by <span class="font-mono">{v.replaced_by_number}</span>{/if}
							{#if v.credit_note_number}{v.replaced_by_number ? ' · ' : ''}credit note <a class="link font-mono" href="/billing/credit-notes?q={encodeURIComponent(v.credit_note_number)}">{v.credit_note_number}</a>{:else}{v.replaced_by_number ? ' · ' : ''}no credit note (zero amount){/if}
						</div>
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>

{#if dialog === 'send'}
	<ConfirmDialog title="Send invoice by email" confirmLabel="Send invoice" onconfirm={call('send', 'The invoice could not be sent.')} onclose={() => (dialog = '')}>
		Email invoice <strong class="font-mono">{inv?.number}</strong> as a PDF to the administrators of <strong>{due.club.name}</strong>?
		{#if inv?.sent_at}It was already sent {dateTime(inv.sent_at)}; they will receive it again.{/if}
	</ConfirmDialog>
{:else if dialog === 'mark'}
	<ConfirmDialog title="Mark invoice as sent" confirmLabel="Mark as sent" onconfirm={call('mark-sent', 'The invoice could not be marked as sent.')} onclose={() => (dialog = '')}>
		Record that invoice <strong class="font-mono">{inv?.number}</strong> was delivered to <strong>{due.club.name}</strong> outside the system. No email is sent.
	</ConfirmDialog>
{:else if dialog === 'void'}
	<VoidReissueDialog {due} ondone={voided} onclose={() => (dialog = '')} />
{:else if dialog === 'issue'}
	<IssueInvoiceDialog {due} {ondone} onclose={() => (dialog = '')} />
{/if}
