<script lang="ts">
	import { money, type Discount } from '#lib/shop.ts';
	let { d }: { d: Pick<Discount, 'discount_type' | 'discount_amount'> } = $props();
	const n = $derived(Number(d.discount_amount));
</script>

<span class="font-mono">{d.discount_type === 'percentage' ? `${Number.isFinite(n) ? n : d.discount_amount}%` : money(d.discount_amount)}</span>
