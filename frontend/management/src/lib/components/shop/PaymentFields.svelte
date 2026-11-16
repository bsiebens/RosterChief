<script module lang="ts">
	export type PaymentDraft = { id: string; amount: string; method: string; reference: string; note: string; voucher_number: string; paid_at: string };
</script>

<script lang="ts">
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { MANUAL_METHODS } from '#lib/shop-orders.ts';

	/** The fields of the record / edit payment forms (posted as plain form fields). `locked`: only the note can change. `noVoucher`: charge invoices cannot be paid with a voucher. */
	let { v = $bindable(), errors = {}, edit = false, locked = false, noVoucher = false }: { v: PaymentDraft; errors?: Record<string, string>; edit?: boolean; locked?: boolean; noVoucher?: boolean } = $props();
	const id = $props.id();
</script>

<div class="grid gap-3 sm:grid-cols-2">
	<Field label={t('shop.pay.amount')} name="amount" type="number" step="0.01" min="0.01" bind:value={v.amount} error={errors.amount} disabled={locked} required />
	<div class="form-control w-full">
		<label class="label" for="{id}-method"><span class="label-text">{t('shop.pay.method')}</span></label>
		<select id="{id}-method" name="method" class="select select-bordered w-full" bind:value={v.method} disabled={locked || edit}>
			{#each MANUAL_METHODS.filter((m) => !(noVoucher && m === 'voucher')) as m (m)}<option value={m}>{t(`shop.pay.method.${m}`)}</option>{/each}
			{#if v.method === 'online'}<option value="online">{t('shop.pay.method.online')}</option>{/if}
		</select>
		{#if errors.method}<span class="mt-1 block text-xs text-error">{errors.method}</span>{/if}
	</div>
	{#if v.method === 'voucher' && !edit}
		<Field label={t('shop.pay.voucher_number')} name="voucher_number" bind:value={v.voucher_number} error={errors.voucher_number} placeholder="VCH-2026-00001" required />
	{/if}
	<Field label={t('shop.pay.reference')} name="reference" bind:value={v.reference} error={errors.reference} disabled={locked} />
	<Field label={t('shop.pay.paid_at')} name="paid_at" type="datetime-local" bind:value={v.paid_at} error={errors.paid_at} disabled={locked} help={t('shop.pay.paid_at_help')} />
	<div class="sm:col-span-2"><Field label={t('shop.pay.note')} name="note" bind:value={v.note} error={errors.note} /></div>
</div>
{#if locked}<p class="text-xs text-muted">{t('shop.pay.locked')}</p>{/if}
