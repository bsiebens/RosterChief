<script lang="ts">
	import { Download, Mail, MailCheck, FilePlus } from '@lucide/svelte';
	import { platform } from '#lib/api.ts';
	import type { Due } from '#lib/billing.ts';
	import Alert from '#lib/components/Alert.svelte';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import IssueInvoiceDialog from './IssueInvoiceDialog.svelte';
	import { dateTime, day, money2, vatPct } from './format.ts';

	let { due, canIssue, ondone }: { due: Due; canIssue: boolean; ondone: () => void } = $props();

	const inv = $derived(due.invoice);
	let dialog = $state<'' | 'send' | 'mark' | 'issue'>('');
	let pdfBusy = $state(false);
	let error = $state('');

	async function openPdf() {
		pdfBusy = true;
		error = '';
		try {
			const res = await fetch(`/api/platform/v1/billing/dues/${due.id}/invoice.pdf`);
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
				a.download = `${inv?.number ?? 'invoice'}.pdf`;
				a.click();
			}
			setTimeout(() => URL.revokeObjectURL(url), 60_000);
		} catch {
			error = 'Could not reach the server. Check your connection and try again.';
		} finally {
			pdfBusy = false;
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

	{#if inv}
		<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
			<dt class="text-muted">Number</dt>
			<dd class="font-mono">{inv.number}{#if inv.custom_number}<span class="badge badge-ghost badge-xs ml-1.5" title="Issued outside the automatic sequence">custom</span>{/if}</dd>
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
			<button class="btn btn-outline btn-sm" onclick={openPdf} disabled={pdfBusy}><Download size={13} aria-hidden="true" /> {pdfBusy ? 'Preparing…' : 'Download PDF'}</button>
			<button class="btn btn-outline btn-sm" onclick={() => (dialog = 'send')}><Mail size={13} aria-hidden="true" /> {inv.sent_at ? 'Send again' : 'Send by email'}</button>
			{#if !inv.sent_at}
				<button class="btn btn-outline btn-sm" onclick={() => (dialog = 'mark')}><MailCheck size={13} aria-hidden="true" /> Mark as sent</button>
			{/if}
		</div>
	{:else}
		<p class="text-sm text-muted">No invoice has been issued for this period yet.</p>
		{#if canIssue}
			<div><button class="btn btn-outline btn-sm" onclick={() => (dialog = 'issue')}><FilePlus size={13} aria-hidden="true" /> Issue invoice</button></div>
		{/if}
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
{:else if dialog === 'issue'}
	<IssueInvoiceDialog {due} {ondone} onclose={() => (dialog = '')} />
{/if}
