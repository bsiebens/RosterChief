<script lang="ts">
	import { enhance } from '$app/forms';
	import { ArrowLeft, Plus, Save, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Field from '#lib/components/Field.svelte';
	import SearchSelect from '#lib/components/SearchSelect.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { stockLabel } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	type Line = { key: number; item: string; qty: string };
	let seq = 1;
	let lines = $state<Line[]>([{ key: 0, item: '', qty: '' }]);
	let note = $state('');
	let tried = $state(false);
	let busy = $state(false);

	const items = $derived(data.rows.map((r) => ({ id: `${r.product_id}|${r.variant_id ?? ''}`, label: stockLabel(r), sub: t('shop.inv.on_hand_n', { n: r.on_hand }) })));
	const filled = $derived(lines.filter((l) => l.item && Number(l.qty) >= 1));
	const payload = $derived(
		JSON.stringify({
			note,
			items: filled.map((l) => {
				const [product_id, variant_id] = l.item.split('|');
				return { product_id, variant_id: variant_id || null, quantity: Number(l.qty) };
			})
		})
	);
	const errorText = $derived(form && 'status' in form && form.status ? form.message || (form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed')) : '');
	const lineErr = (l: Line) => (tried && l.item === '' && l.qty !== '' ? t('shop.inv.err.item') : tried && l.item !== '' && !(Number(l.qty) >= 1) ? t('shop.inv.err.qty') : '');

	usePageHeader(() => ({ title: t('shop.inv.receive_title'), context: t('shop.nav.inventory'), actions }));
</script>

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/inventory"><ArrowLeft size={16} /> {t('shop.inv.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.inv.receive_title'))}</title></svelte:head>

<form
	method="POST"
	action="?/save"
	class="grid gap-5"
	novalidate
	use:enhance={({ cancel }) => {
		tried = true;
		if (!filled.length || lines.some((l) => lineErr(l))) {
			cancel();
			return;
		}
		busy = true;
		return async ({ update }) => {
			await update({ reset: false });
			busy = false;
		};
	}}>
	<input type="hidden" name="payload" value={payload} />
	{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}
	{#if !data.rows.length}<Alert kind="info">{t('shop.inv.receive_none')}</Alert>{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<section class="card card-body gap-3 xl:col-span-2">
			<p class="text-sm text-muted">{t('shop.inv.receive_help')}</p>
			{#each lines as l (l.key)}
				<div class="grid items-start gap-3 md:grid-cols-[1fr_9rem_auto]">
					<div>
						<span class="label"><span class="label-text">{t('shop.col.product')}</span></span>
						<SearchSelect {items} bind:value={l.item} label={t('shop.col.product')} placeholder={t('shop.inv.pick_product')} error={lineErr(l) && !l.item ? lineErr(l) : undefined} />
					</div>
					<Field label={t('shop.inv.quantity')} type="number" min="1" step="1" bind:value={l.qty} error={l.item ? lineErr(l) : ''} />
					<div class="form-control">
						<span class="label" aria-hidden="true"><span class="label-text invisible">&nbsp;</span></span>
						<button class="btn btn-ghost btn-square" type="button" aria-label={t('common.delete')} disabled={lines.length === 1} onclick={() => (lines = lines.filter((x) => x.key !== l.key))}><X size={16} /></button>
					</div>
				</div>
			{/each}
			<div><button class="btn btn-outline btn-sm gap-2" type="button" onclick={() => lines.push({ key: seq++, item: '', qty: '' })}><Plus size={14} /> {t('shop.inv.add_line')}</button></div>
			{#if tried && !filled.length}<p class="text-xs text-error">{t('shop.inv.err.none')}</p>{/if}
		</section>
		<div class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body gap-3">
				<Field label={t('shop.inv.note')} bind:value={note} maxlength={255} help={t('shop.inv.note_help')} />
				<div class="grid gap-2">
					<button class="btn btn-primary w-full gap-2" type="submit" disabled={busy}><Save size={16} /> {t('shop.inv.receive_save')}</button>
					<a class="btn btn-outline w-full" href="/shop/inventory">{t('common.cancel')}</a>
				</div>
			</section>
		</div>
	</div>
</form>
