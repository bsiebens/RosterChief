<script lang="ts">
	import { Plus, Trash2 } from '@lucide/svelte';
	import Field from '#lib/components/Field.svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { ProductIn } from '#lib/shop.ts';

	/**
	 * The registration pricing of a membership product: the season it belongs to, an early-bird discount (only if paid by the deadline)
	 * and a discount for registering several people at once (tiers by number of registrants, per person or once per order).
	 * `fields` are the API's errors, keyed like `registrant_tiers.0.min_registrants`.
	 */
	let { v = $bindable(), seasons, teams = [], volunteer = false, fields = {} }: { v: ProductIn; seasons: { id: string; name: string }[]; teams?: { id: string; name: string }[]; volunteer?: boolean; fields?: Record<string, string> } = $props();
	const uid = $props.id();
	const tierErr = (i: number, name: string) => fields[`registrant_tiers.${i}.${name}`] ?? '';
	function addTier() {
		const used = new Set(v.registrant_tiers.map((x) => x.min_registrants));
		let n = 2;
		while (used.has(n)) n++;
		v.registrant_tiers = [...v.registrant_tiers, { min_registrants: n, discount_type: 'percentage', discount_amount: '' }];
	}
	const earlyOn = $derived(!!v.early_bird_discount_type);
	// A product moved to the Volunteer category keeps no team (the server would refuse it).
	$effect(() => {
		if (volunteer && v.team_id) v.team_id = null;
	});
</script>

<section class="grid gap-4 rounded-lg border border-line p-4" aria-labelledby="{uid}-h">
	<header class="grid gap-0.5">
		<h3 id="{uid}-h" class="font-display text-xs font-bold tracking-[.08em] text-ink uppercase">{t('shop.reg.title')}</h3>
		<p class="text-xs text-muted">{t('shop.reg.help')}</p>
	</header>

	<div class="form-control w-full">
		<label class="label" for="{uid}-season"><span class="label-text">{t('shop.reg.season')}</span></label>
		<select id="{uid}-season" class="select select-bordered w-full" value={v.season_id ?? ''} onchange={(e) => (v.season_id = e.currentTarget.value || null)}>
			<option value="">{t('shop.reg.no_season')}</option>
			{#each seasons as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
		</select>
		<span class="label-text-alt mt-1 block {fields.season_id ? 'text-error' : ''}">{fields.season_id || t('shop.reg.season_help')}</span>
	</div>

	{#if !volunteer && teams.length}
		<div class="form-control w-full">
			<label class="label" for="{uid}-team"><span class="label-text">{t('shop.reg.team')}</span></label>
			<select id="{uid}-team" class="select select-bordered w-full" value={v.team_id ?? ''} onchange={(e) => (v.team_id = e.currentTarget.value || null)}>
				<option value="">{t('shop.reg.no_team')}</option>
				{#each teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
			</select>
			<span class="label-text-alt mt-1 block {fields.team_id ? 'text-error' : ''}">{fields.team_id || t('shop.reg.team_help')}</span>
		</div>
	{/if}

	<div class="grid gap-3">
		<h4 class="text-sm font-semibold text-ink">{t('shop.reg.early_title')}</h4>
		<div class="grid gap-3 md:grid-cols-3">
			<div class="form-control w-full">
				<label class="label" for="{uid}-et"><span class="label-text">{t('shop.reg.early_type')}</span></label>
				<select id="{uid}-et" class="select select-bordered w-full" bind:value={v.early_bird_discount_type}>
					<option value="">{t('shop.reg.none')}</option>
					<option value="percentage">{t('shop.reg.type.percentage')}</option>
					<option value="fixed_amount">{t('shop.reg.type.fixed_amount')}</option>
				</select>
			</div>
			<Field label={t('shop.reg.early_amount')} type="number" step="0.01" min="0" inputmode="decimal" bind:value={v.early_bird_discount_amount} disabled={!earlyOn} error={fields.early_bird_discount_amount} />
			<Field label={t('shop.reg.early_deadline')} type="date" value={v.early_bird_deadline ?? ''} oninput={(e) => (v.early_bird_deadline = (e.currentTarget as HTMLInputElement).value || null)} disabled={!earlyOn} error={fields.early_bird_deadline} help={t('shop.reg.early_help')} />
		</div>
	</div>

	<div class="grid gap-3">
		<div class="flex flex-wrap items-end justify-between gap-3">
			<div>
				<h4 class="text-sm font-semibold text-ink">{t('shop.reg.tiers_title')}</h4>
				<p class="text-xs text-muted">{t('shop.reg.tiers_help')}</p>
			</div>
			<div class="form-control">
				<label class="label py-0" for="{uid}-scope"><span class="label-text text-xs">{t('shop.reg.scope')}</span></label>
				<select id="{uid}-scope" class="select select-bordered select-sm" bind:value={v.registrant_discount_scope} disabled={!v.registrant_tiers.length}>
					<option value="per_person">{t('shop.reg.scope.per_person')}</option>
					<option value="per_order">{t('shop.reg.scope.per_order')}</option>
				</select>
			</div>
		</div>
		{#if fields.registrant_tiers}<span class="text-xs text-error">{fields.registrant_tiers}</span>{/if}
		<ul class="grid gap-2">
			{#each v.registrant_tiers as tier, i (i)}
				<li class="grid items-end gap-2 sm:grid-cols-[8rem_1fr_9rem_auto]">
					<Field label={t('shop.reg.tier_min')} type="number" min="2" step="1" inputmode="numeric" value={String(tier.min_registrants)} oninput={(e) => (tier.min_registrants = Number((e.currentTarget as HTMLInputElement).value))} error={tierErr(i, 'min_registrants')} />
					<div class="form-control w-full">
						<label class="label" for="{uid}-tt-{i}"><span class="label-text">{t('shop.reg.early_type')}</span></label>
						<select id="{uid}-tt-{i}" class="select select-bordered w-full" bind:value={tier.discount_type}>
							<option value="percentage">{t('shop.reg.type.percentage')}</option>
							<option value="fixed_amount">{t('shop.reg.type.fixed_amount')}</option>
						</select>
					</div>
					<Field label={t('shop.reg.early_amount')} type="number" step="0.01" min="0" inputmode="decimal" bind:value={tier.discount_amount} error={tierErr(i, 'discount_amount')} />
					<button class="btn btn-outline btn-error btn-square mb-0.5" type="button" aria-label={t('shop.reg.tier_remove', { n: tier.min_registrants })} onclick={() => (v.registrant_tiers = v.registrant_tiers.filter((_, k) => k !== i))}><Trash2 size={16} /></button>
				</li>
			{/each}
		</ul>
		<button class="btn btn-outline btn-sm w-fit gap-1" type="button" onclick={addTier}><Plus size={14} /> {t('shop.reg.tier_add')}</button>
	</div>
</section>
