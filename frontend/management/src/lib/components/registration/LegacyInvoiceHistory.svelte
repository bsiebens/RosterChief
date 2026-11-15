<script lang="ts">
	import { FileDown, ReceiptText } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { invoicePdfHref, type LegacyInvoice } from '#lib/registrations.ts';
	import { money } from '#lib/shop.ts';

	let { invoice, invoiceNumber, downloadId }: { invoice: LegacyInvoice; invoiceNumber: string | null; downloadId: string | null } = $props();
</script>

<section class="card card-body gap-3">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h2 class="flex items-center gap-2 font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase"><ReceiptText size={16} aria-hidden="true" /> {t('legacy_invoice.title')}</h2>
		<div class="flex items-center gap-3">
			{#if invoiceNumber}<span class="font-mono text-xs text-muted">{invoiceNumber}</span>{/if}
			{#if downloadId}<a class="btn btn-outline btn-sm" href={invoicePdfHref(downloadId)}><FileDown size={14} aria-hidden="true" /> {t('fin.act.pdf')}</a>{/if}
		</div>
	</div>
	<p class="text-sm text-muted">{t('legacy_invoice.description')}</p>
	<div class="overflow-x-auto">
		<table class="table">
			<thead><tr><th>{t('fin.person')}</th><th>{t('reg.line.requested')}</th><th class="text-right">{t('reg.line.base')}</th><th class="text-right">{t('legacy_invoice.discount')}</th></tr></thead>
			<tbody>
				{#each invoice.lines as line (line.id)}
					<tr>
						<td>{#if line.member_id}<a class="link" href="/members/{line.member_id}">{line.member}</a>{:else}{line.member}{/if}</td>
						<td>{line.description}{#if line.excluded_from_invoice}<span class="badge badge-sm badge-outline ms-2">{t('legacy_invoice.excluded')}</span>{/if}</td>
						<td class="text-right font-mono">{money(line.price)}</td>
						<td class="text-right font-mono">{money(line.discount_amount)}</td>
					</tr>
				{/each}
			</tbody>
			<tfoot>
				{#if Number(invoice.manual_discount_amount)}
					<tr><th colspan="3">{t('legacy_invoice.manual_discount')}</th><td class="text-right font-mono">{money(invoice.manual_discount_amount)}</td></tr>
				{/if}
				<tr><th colspan="3">{t('legacy_invoice.total')}</th><td class="text-right font-mono font-bold">{money(invoice.total)}</td></tr>
			</tfoot>
		</table>
	</div>
</section>
