<script lang="ts">
	import { Plus, Trash2, X } from '@lucide/svelte';
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { VariantIn } from '#lib/shop.ts';

	/** The variant rows on the add page (sent along with the product; blank names are skipped). */
	let { variants = $bindable(), membership = false, teams = [] }: { variants: VariantIn[]; membership?: boolean; teams?: { id: string; name: string }[] } = $props();
</script>

<div class="grid gap-3">
	{#each variants as row, i (i)}
		<div class="grid items-end gap-3 {teams.length ? 'md:grid-cols-[1fr_12rem_12rem_auto]' : 'md:grid-cols-[1fr_12rem_auto]'}">
			<Field label={t('shop.variant.f.name')} bind:value={row.name} placeholder={t('shop.variant.placeholder')} maxlength={255} />
			<Field label={t('shop.variant.f.price')} type="number" step="0.01" min={membership ? undefined : '0'} placeholder={t('shop.variant.same_price')} value={row.price ?? ''} oninput={(e) => (row.price = e.currentTarget.value || null)} />
			{#if teams.length}
				<div class="form-control w-full">
					<label class="label" for="vt-{i}"><span class="label-text">{t('shop.variant.f.team')}</span></label>
					<select id="vt-{i}" class="select select-bordered w-full" value={row.team_id ?? ''} onchange={(e) => (row.team_id = e.currentTarget.value || null)}>
						<option value="">{t('shop.variant.same_team')}</option>
						{#each teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
					</select>
				</div>
			{/if}
			<button class="btn btn-ghost btn-square" type="button" aria-label={t('common.delete')} onclick={() => variants.splice(i, 1)}><X size={16} /></button>
		</div>
	{/each}
	<div><button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => variants.push({ name: '', price: null, team_id: null })}><Plus size={14} /> {t('shop.variant.add')}</button></div>
</div>
