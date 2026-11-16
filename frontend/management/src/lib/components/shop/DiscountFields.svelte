<script lang="ts">
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { DiscountIn } from '#lib/shop.ts';

	/** All the fields of a discount code (add page and editor), in two columns. */
	let { v = $bindable(), fields = {}, tried = false }: { v: DiscountIn; fields?: Record<string, string>; tried?: boolean } = $props();
	const uid = $props.id();
	const amountNum = $derived(Number(v.discount_amount));
	const nameErr = $derived(fields.name || (tried && !v.name.trim() ? t('shop.disc.err.name') : ''));
	const codeErr = $derived(fields.code || (tried && !v.code.trim() ? t('shop.disc.err.code') : ''));
	const amountErr = $derived(fields.discount_amount || (tried && !(amountNum > 0) ? t('shop.disc.err.amount') : tried && v.discount_type === 'percentage' && amountNum > 100 ? t('shop.disc.err.percent') : ''));
	const untilErr = $derived(fields.valid_until || (v.valid_from && v.valid_until && v.valid_until < v.valid_from ? t('shop.disc.err.window') : ''));
</script>

<div class="grid gap-4">
	<div class="grid gap-4 md:grid-cols-2">
		<Field label={t('shop.disc.f.name')} bind:value={v.name} error={nameErr} required maxlength={255} />
		<Field label={t('shop.disc.f.code')} bind:value={v.code} error={codeErr} required maxlength={50} style="text-transform:uppercase" help={t('shop.disc.f.code_help')} />
		<div class="form-control w-full">
			<label class="label" for="{uid}-type"><span class="label-text">{t('shop.disc.f.type')}</span></label>
			<select id="{uid}-type" class="select select-bordered w-full" bind:value={v.discount_type}>
				<option value="percentage">{t('shop.disc.type.percentage')}</option>
				<option value="fixed_amount">{t('shop.disc.type.fixed_amount')}</option>
			</select>
		</div>
		<Field label={v.discount_type === 'percentage' ? t('shop.disc.f.percent') : t('shop.disc.f.amount')} type="number" step="0.01" min="0" bind:value={v.discount_amount} error={amountErr} required />
		<Field label={t('shop.disc.f.valid_from')} type="date" value={v.valid_from ?? ''} oninput={(e) => (v.valid_from = e.currentTarget.value || null)} error={fields.valid_from} />
		<Field label={t('shop.disc.f.valid_until')} type="date" value={v.valid_until ?? ''} oninput={(e) => (v.valid_until = e.currentTarget.value || null)} error={untilErr} />
		<Field label={t('shop.disc.f.max_uses')} type="number" min="1" step="1" value={v.max_uses === null ? '' : String(v.max_uses)} oninput={(e) => (v.max_uses = e.currentTarget.value ? Number(e.currentTarget.value) : null)} error={fields.max_uses} help={t('shop.disc.f.max_uses_help')} />
	</div>
	<div class="form-control w-full">
		<label class="label" for="{uid}-desc"><span class="label-text">{t('shop.disc.f.description')}</span></label>
		<textarea id="{uid}-desc" class="textarea textarea-bordered min-h-20 w-full" bind:value={v.description}></textarea>
	</div>
	<div class="grid gap-4 md:grid-cols-2">
		<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={v.once_per_member} />
			<span class="grid gap-0.5"><span class="text-sm font-semibold text-ink">{t('shop.disc.f.once')}</span><span class="text-xs text-muted">{t('shop.disc.f.once_help')}</span></span>
		</label>
		<label class="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
			<input type="checkbox" class="checkbox mt-0.5" bind:checked={v.is_active} />
			<span class="grid gap-0.5"><span class="text-sm font-semibold text-ink">{t('shop.disc.f.is_active')}</span><span class="text-xs text-muted">{t('shop.disc.f.is_active_help')}</span></span>
		</label>
	</div>
</div>
