<script lang="ts">
	import { Percent } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { price } from '#lib/components/billing/cycles/fmt.ts';

	let { rate }: { rate: string | null } = $props();

	const net = 50;
	const r = $derived(rate == null ? null : Number(rate));
	const vat = $derived(r == null ? 0 : Math.round(net * r) / 100);
</script>

{#if r == null}
	<Alert kind="warning">The VAT rate could not be loaded. See <a class="link font-semibold" href="/billing/settings">Invoicing</a>.</Alert>
{:else}
	<div class="card">
		<div class="card-body flex-row flex-wrap items-center gap-x-6 gap-y-2 p-4">
			<Percent size={16} class="text-dim" aria-hidden="true" />
			<div class="text-sm">
				Prices on this page are <strong>net</strong>. VAT is added at <strong class="font-mono">{price(r)}%</strong> when a period is opened, for example <span class="font-mono">{price(net)} + {r}% = {price(net + vat)}</span>.
			</div>
			<a class="link ml-auto text-sm font-semibold" href="/billing/settings">Change the rate in Invoicing</a>
		</div>
	</div>
{/if}
