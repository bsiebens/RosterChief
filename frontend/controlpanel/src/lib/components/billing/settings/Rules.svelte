<script lang="ts">
	import Alert from '#lib/components/Alert.svelte';
</script>

<Alert kind="info" title="How invoicing works">
	<ul class="mt-1 list-disc space-y-1 pl-4">
		<li>Changes only affect invoices issued afterwards. Each invoice stores a snapshot of the issuer details and amounts at the moment it is issued.</li>
		<li>The VAT rate is applied when a period is opened and is then frozen on that period.</li>
		<li>Reverse charge (0% VAT) applies automatically to clubs with an EU VAT number in another EU country than the issuer. This needs the issuer's country.</li>
		<li>Invoice and credit note numbers come from an automatic gapless sequence in a format you configure below (a pattern such as INV-{'{YYYY}'}-{'{NNNNN}'}). The counter restarts with the finest date placeholder in the pattern. A custom invoice number is only possible when a person opens a period by hand (start billing, open next period now, change plan) or issues an invoice that was created outside this system; renewals are always automatic.</li>
		<li>Lifecycle of an unpaid period: the invoice is created in advance, then the grace period runs (counted from the later of the period start and the invoice date), then the club is downgraded to the free plan, and archived after the “days between downgrade and archive” below, but only if that club has auto-archive on. Paying or waiving the overdue periods restores the club at once.</li>
	</ul>
</Alert>
