<script lang="ts">
	import { Check, Pencil, Plus, Power, Trash2, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { money, type Variant } from '#lib/shop.ts';

	/** Manage the variants of a saved product. Every action calls the API from the browser and updates `variants`. */
	let { productId, variants = $bindable(), price, inventoryManaged, membership = false, teams = [] }: { productId: string; variants: Variant[]; price: string; inventoryManaged: boolean; membership?: boolean; teams?: { id: string; name: string }[] } = $props();

	let busy = $state(false);
	let errorMsg = $state('');
	let blocked = $state<Variant | null>(null);
	let editing = $state<string | null>(null); // variant id, or 'new'
	let draft = $state({ name: '', price: '', team_id: '' });
	let fieldErr = $state<Record<string, string>>({});

	function startEdit(v: Variant | null) {
		editing = v?.id ?? 'new';
		draft = { name: v?.name ?? '', price: v?.price ?? '', team_id: v?.team_id ?? '' };
		fieldErr = {};
		errorMsg = '';
	}
	const err = (r: { message?: string; fields: Record<string, string> }) => (errorMsg = r.fields.__all__ || r.message || t('common.error.generic'));

	async function save() {
		if (!draft.name.trim()) {
			fieldErr = { name: t('shop.variant.err.name') };
			return;
		}
		busy = true;
		errorMsg = '';
		const body = { name: draft.name.trim(), price: draft.price === '' ? null : draft.price, ...(teams.length ? { team_id: draft.team_id || null } : {}) };
		const isNew = editing === 'new';
		const res = await api<Variant>(isNew ? 'POST' : 'PATCH', isNew ? `/shop/products/${productId}/variants` : `/shop/products/${productId}/variants/${editing}`, { body });
		busy = false;
		if (!res.ok || !res.data) {
			fieldErr = res.fields;
			if (!Object.keys(res.fields).length) err(res);
			return;
		}
		variants = isNew ? [...variants, res.data] : variants.map((x) => (x.id === res.data!.id ? res.data! : x));
		editing = null;
	}
	async function setActive(v: Variant, is_active: boolean) {
		busy = true;
		errorMsg = '';
		blocked = null;
		const res = await api<Variant>('PATCH', `/shop/products/${productId}/variants/${v.id}`, { body: { is_active } });
		busy = false;
		if (res.ok && res.data) variants = variants.map((x) => (x.id === v.id ? res.data! : x));
		else err(res);
	}
	async function remove(v: Variant) {
		busy = true;
		errorMsg = '';
		blocked = null;
		const res = await api('DELETE', `/shop/products/${productId}/variants/${v.id}`);
		busy = false;
		if (res.ok) variants = variants.filter((x) => x.id !== v.id);
		else if (res.status === 409) blocked = v;
		else err(res);
	}
</script>

<div class="grid gap-3">
	{#if errorMsg}<Alert kind="error">{errorMsg}</Alert>{/if}
	{#if blocked}
		<Alert kind="warning" title={t('shop.variant.blocked_title')}>
			<p>{t('shop.variant.blocked', { name: blocked.name })}</p>
			{#if blocked.is_active}<button class="btn btn-outline btn-sm mt-2" type="button" disabled={busy} onclick={() => blocked && setActive(blocked, false)}>{t('shop.variant.deactivate')}</button>{/if}
		</Alert>
	{/if}
	{#if variants.length}
		<div class="overflow-x-auto">
			<table class="table">
				<thead><tr><th>{t('shop.variant.f.name')}</th><th class="text-right">{t('shop.col.price')}</th>{#if teams.length}<th>{t('shop.variant.f.team')}</th>{/if}{#if inventoryManaged}<th class="text-right">{t('shop.col.stock')}</th>{/if}<th><span class="sr-only">{t('shop.col.actions')}</span></th></tr></thead>
				<tbody>
					{#each variants as v (v.id)}
						<tr class={v.is_active ? '' : 'opacity-60'}>
							{#if editing === v.id}
								<td colspan={2 + (teams.length ? 1 : 0) + (inventoryManaged ? 1 : 0)}>{@render editor()}</td>
								<td></td>
							{:else}
								<td class="font-semibold">{v.name}{#if !v.is_active} <span class="badge badge-sm badge-warning ml-1">{t('shop.inactive')}</span>{/if}</td>
								<td class="text-right font-mono text-sm">{money(v.effective_price)}{#if v.price === null}<span class="ml-1 text-xs text-muted">({t('shop.variant.product_price')})</span>{/if}</td>
								{#if teams.length}<td class="text-sm">{#if v.team_name}{v.team_name}{:else}<span class="text-muted">{t('shop.variant.same_team')}</span>{/if}</td>{/if}
								{#if inventoryManaged}<td class="text-right font-mono text-sm {v.in_stock ? '' : 'text-error'}">{v.stock ?? 0}</td>{/if}
								<td class="text-right whitespace-nowrap">
									<div class="flex justify-end gap-1">
										<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={t('common.edit')} disabled={busy} onclick={() => startEdit(v)}><Pencil size={13} /></button>
										<button class="btn btn-outline btn-xs btn-square" type="button" aria-label={v.is_active ? t('shop.deactivate') : t('shop.activate')} disabled={busy} onclick={() => setActive(v, !v.is_active)}><Power size={13} /></button>
										<button class="btn btn-outline btn-error btn-xs btn-square" type="button" aria-label={t('common.delete')} disabled={busy} onclick={() => remove(v)}><Trash2 size={13} /></button>
									</div>
								</td>
							{/if}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<p class="text-sm text-muted">{t('shop.variant.empty')}</p>
	{/if}
	{#if editing === 'new'}{@render editor()}{:else}
		<div><button class="btn btn-outline btn-sm gap-2" type="button" disabled={busy} onclick={() => startEdit(null)}><Plus size={14} /> {t('shop.variant.add')}</button></div>
	{/if}
	{#if inventoryManaged && variants.some((v) => v.is_active)}<p class="text-xs text-muted">{t('shop.variant.stock_note')}</p>{/if}
</div>

{#snippet editor()}
	<div class="grid items-start gap-3 {teams.length ? 'md:grid-cols-[1fr_12rem_12rem_auto]' : 'md:grid-cols-[1fr_12rem_auto]'}">
		<Field label={t('shop.variant.f.name')} bind:value={draft.name} error={fieldErr.name} maxlength={255} />
		<Field label={t('shop.variant.f.price')} type="number" step="0.01" min={membership ? undefined : '0'} bind:value={draft.price} error={fieldErr.price} placeholder={money(price)} />
		{#if teams.length}
			<div class="form-control w-full">
				<label class="label" for="vt-edit"><span class="label-text">{t('shop.variant.f.team')}</span></label>
				<select id="vt-edit" class="select select-bordered w-full" bind:value={draft.team_id}>
					<option value="">{t('shop.variant.same_team')}</option>
					{#each teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
				</select>
				{#if fieldErr.team_id}<span class="mt-1 block text-xs text-error">{fieldErr.team_id}</span>{/if}
			</div>
		{/if}
		<div class="flex gap-1 md:mt-9">
			<button class="btn btn-primary btn-square" type="button" aria-label={t('common.save')} disabled={busy} onclick={save}><Check size={16} /></button>
			<button class="btn btn-ghost btn-square" type="button" aria-label={t('common.cancel')} onclick={() => (editing = null)}><X size={16} /></button>
		</div>
	</div>
{/snippet}
