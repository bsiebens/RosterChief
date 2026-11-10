<script lang="ts">
	import Field from '#lib/components/Field.svelte';
	import ProductRegistrationFields from '#lib/components/shop/ProductRegistrationFields.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { PRODUCT_TYPES, type Category, type ProductIn } from '#lib/shop.ts';

	/** Name, category, price, type, description and the switches of a product (add page and editor). */
	let { v = $bindable(), categories, seasons = [], teams = [], fields = {}, tried = false }: { v: ProductIn; categories: Category[]; seasons?: { id: string; name: string }[]; teams?: { id: string; name: string }[]; fields?: Record<string, string>; tried?: boolean } = $props();
	const uid = $props.id();
	const nameErr = $derived(fields.name || (tried && !v.name.trim() ? t('shop.product.err.name') : ''));
	// Only membership products may be negative: that is a credit on a registration (e.g. for volunteers).
	const membership = $derived(v.product_type === 'membership');
	// Volunteers are not placed in a team: the Volunteer category has no registration team.
	const volunteer = $derived(categories.find((c) => c.id === v.category_id)?.system_key === 'volunteer');
	const priceErr = $derived(fields.price || (tried && (v.price === '' || (!membership && Number(v.price) < 0)) ? t('shop.product.err.price') : ''));
	const flags = $derived([
		{ k: 'is_active' as const, label: t('shop.product.f.is_active'), help: t('shop.product.f.is_active_help') },
		{ k: 'is_public' as const, label: t('shop.product.f.is_public'), help: t('shop.product.f.is_public_help') },
		{ k: 'personalization_enabled' as const, label: t('shop.product.f.personalization'), help: t('shop.product.f.personalization_help') },
		{ k: 'inventory_managed' as const, label: t('shop.product.f.inventory'), help: t('shop.product.f.inventory_help'), disabled: v.product_type !== 'merchandise', err: fields.inventory_managed }
	]);
</script>

<div class="grid gap-4">
	<div class="grid gap-4 md:grid-cols-2">
		<Field label={t('shop.product.f.name')} bind:value={v.name} error={nameErr} required maxlength={255} />
		<Field label={t('shop.product.f.price')} type="number" step="0.01" min={membership ? undefined : '0'} inputmode="decimal" bind:value={v.price} error={priceErr} help={membership ? t('shop.product.price_credit_hint') : undefined} required />
		<div class="form-control w-full">
			<label class="label" for="{uid}-cat"><span class="label-text">{t('shop.product.f.category')}</span></label>
			<select id="{uid}-cat" class="select select-bordered w-full" value={v.category_id ?? ''} onchange={(e) => (v.category_id = e.currentTarget.value || null)}>
				<option value="">{t('shop.product.no_category')}</option>
				{#each categories as c (c.id)}<option value={c.id}>{c.name}{c.is_system ? ` (${t('shop.cat.system_short')})` : ''}</option>{/each}
			</select>
			{#if fields.category_id}<span class="mt-1 block text-xs text-error">{fields.category_id}</span>
			{:else if v.product_type === 'membership'}<span class="label-text-alt mt-1 block">{t('shop.cat.membership_hint')}</span>{/if}
		</div>
		<div class="form-control w-full">
			<label class="label" for="{uid}-type"><span class="label-text">{t('shop.product.f.type')}</span></label>
			<select id="{uid}-type" class="select select-bordered w-full" bind:value={v.product_type} onchange={() => v.product_type !== 'merchandise' && (v.inventory_managed = false)}>
				{#each PRODUCT_TYPES as p (p)}<option value={p}>{t(`shop.type.${p}`)}</option>{/each}
			</select>
		</div>
	</div>
	<div class="form-control w-full">
		<label class="label" for="{uid}-desc"><span class="label-text">{t('shop.product.f.description')}</span></label>
		<textarea id="{uid}-desc" class="textarea textarea-bordered min-h-24 w-full" bind:value={v.description}></textarea>
		{#if fields.description}<span class="mt-1 block text-xs text-error">{fields.description}</span>{/if}
	</div>
	<div class="grid gap-4 md:grid-cols-2">
		{#each flags as f (f.k)}
			<label class="flex items-start gap-3 rounded-lg border border-line p-3 {f.disabled ? 'opacity-50' : 'cursor-pointer'}">
				<input type="checkbox" class="checkbox mt-0.5" bind:checked={v[f.k]} disabled={f.disabled} />
				<span class="grid gap-0.5">
					<span class="text-sm font-semibold text-ink">{f.label}</span>
					<span class="text-xs text-muted">{f.err || f.help}</span>
				</span>
			</label>
		{/each}
	</div>
	{#if v.product_type === 'membership'}<ProductRegistrationFields bind:v {seasons} {teams} {volunteer} {fields} />{/if}
</div>
