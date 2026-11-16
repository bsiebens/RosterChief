<script lang="ts">
	import { X } from '@lucide/svelte';
	import EntityPicker from '#lib/components/EntityPicker.svelte';
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { VoucherIn } from '#lib/shop.ts';

	/** Amount, expiry, holder, notes (and the count when generating several). `holder` is the current holder's name when editing. */
	let { v = $bindable(), fields = {}, tried = false, bulk = false, holder = '' }: { v: VoucherIn; fields?: Record<string, string>; tried?: boolean; bulk?: boolean; holder?: string } = $props();
	const uid = $props.id();
	let changing = $state(false);
	// svelte-ignore state_referenced_locally
	let picked = $state(v.issued_to_id ?? '');
	$effect(() => {
		if (picked) v.issued_to_id = picked;
		else if (!showChip) v.issued_to_id = null;
	});
	const amountErr = $derived(fields.amount || (tried && !(Number(v.amount) > 0) ? t('shop.vch.err.amount') : ''));
	const expiryErr = $derived(fields.expiry_date || (tried && !v.expiry_date ? t('shop.vch.err.expiry') : ''));
	const countErr = $derived(fields.count || (tried && ((v.count ?? 1) < 1 || (v.count ?? 1) > 200) ? t('shop.vch.err.count') : ''));
	const showChip = $derived(!!holder && !!v.issued_to_id && !changing);
</script>

<div class="grid gap-4">
	<div class="grid gap-4 md:grid-cols-2">
		<Field label={t('shop.vch.amount')} type="number" step="0.01" min="0.01" bind:value={v.amount} error={amountErr} required />
		<Field label={t('shop.vch.expiry')} type="date" bind:value={v.expiry_date} error={expiryErr} required />
		{#if bulk}<Field label={t('shop.vch.f.count')} type="number" min="1" max="200" step="1" value={String(v.count ?? 1)} oninput={(e) => (v.count = Number(e.currentTarget.value) || 1)} error={countErr} help={t('shop.vch.f.count_help')} />{/if}
		<div class="form-control w-full">
			{#if showChip}
				<span class="label"><span class="label-text">{t('shop.vch.holder')}</span></span>
				<div class="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-2">
					<span class="font-semibold text-ink">{holder}</span>
					<button class="btn btn-ghost btn-xs" type="button" aria-label={t('common.clear')} onclick={() => ((v.issued_to_id = null), (picked = ''), (changing = true))}><X size={14} /></button>
				</div>
			{:else}
				<EntityPicker kind="member" name="issued_to_id" label={t('shop.vch.holder')} bind:value={picked} error={fields.issued_to_id} />
			{/if}
			<span class="label-text-alt mt-1 block">{t('shop.vch.f.holder_help')}</span>
		</div>
	</div>
	<div class="form-control w-full">
		<label class="label" for="{uid}-notes"><span class="label-text">{t('shop.vch.notes')}</span></label>
		<textarea id="{uid}-notes" class="textarea textarea-bordered min-h-20 w-full" bind:value={v.notes}></textarea>
	</div>
</div>
