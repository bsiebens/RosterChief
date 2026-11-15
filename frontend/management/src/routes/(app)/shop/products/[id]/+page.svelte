<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { Images, Layers, Paperclip, Settings2, ArrowLeft, Save, Trash2, ClipboardList } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import ProductFields from '#lib/components/shop/ProductFields.svelte';
	import ProductAttachments from '#lib/components/shop/ProductAttachments.svelte';
	import ProductImages from '#lib/components/shop/ProductImages.svelte';
	import ProductVariants from '#lib/components/shop/ProductVariants.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { productInOf, productPayload, type ProductIn } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.item);
	const formId = $props.id();

	// svelte-ignore state_referenced_locally
	let v = $state<ProductIn>(productInOf(data.item));
	// A variant can name its own team on membership products outside the Volunteer category.
	const teamsForVariants = $derived(v.product_type === 'membership' && data.categories.find((c) => c.id === v.category_id)?.system_key !== 'volunteer' ? data.teams : []);
	// svelte-ignore state_referenced_locally
	let variants = $state(data.item.variants);
	// svelte-ignore state_referenced_locally
	let images = $state(data.item.images ?? []);
	// svelte-ignore state_referenced_locally
	let attachments = $state(data.item.attachments ?? []);
	// A reload after a save brings the stored product: start the editor from it again.
	$effect(() => {
		const stored = data.item;
		untrack(() => {
			v = productInOf(stored);
			variants = stored.variants;
			images = stored.images ?? [];
			attachments = stored.attachments ?? [];
		});
	});

	const dirty = $derived(JSON.stringify(v) !== JSON.stringify(productInOf(item)));
	let tried = $state(false);
	let busy = $state(false);
	let leaving = $state(false);
	let deleteOpen = $state(false);

	// Unsaved-changes guard: not while saving, and not for a refresh of this very page.
	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('shop.unsaved_confirm'))) cancel();
	});

	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const blocked = $derived(!!form && 'code' in form && ['has_orders', 'has_stock_history'].includes(String(form.code)));
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const errorText = $derived.by(() => {
		if (!form || !('status' in form) || !form.status || blocked) return '';
		if (form.message) return String(form.message);
		return form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed');
	});
	const valid = () => !!v.name.trim() && v.price !== '' && (v.product_type === 'membership' || Number(v.price) >= 0);

	usePageHeader(() => ({ title: t('shop.product.title_edit'), context: data.item.name, actions }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/products"><ArrowLeft size={16} /> {t('shop.product.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.product.title_edit'))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created && !dirty}<Alert kind="success">{t('shop.product.created')}</Alert>{/if}
	{#if done === 'save'}<Alert kind="success">{t('shop.saved')}</Alert>{/if}
	{#if done === 'deactivate'}<Alert kind="success">{t('shop.product.deactivated')}</Alert>{/if}
	{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}
	{#if blocked}
		<Alert kind="warning" title={t('shop.product.delete.blocked_title')}>
			<p>{t('shop.product.delete.blocked', { name: item.name })}</p>
			{#if item.is_active}
				<form method="POST" action="?/deactivate" class="mt-2" use:enhance><button class="btn btn-outline btn-sm" type="submit">{t('shop.deactivate')}</button></form>
			{/if}
		</Alert>
	{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<form
				id={formId}
				method="POST"
				action="?/save"
				class="grid gap-5"
				novalidate
				use:enhance={({ cancel }) => {
					if (!valid()) {
						tried = true;
						cancel();
						return;
					}
					busy = true;
					leaving = true; // the save and the reload after it are not "leaving the page"
					return async ({ update }) => {
						await update({ reset: false });
						busy = false;
						leaving = false;
						tried = false;
					};
				}}>
				<input type="hidden" name="payload" value={JSON.stringify(productPayload(v))} />
				<section class="card card-body card-hl gap-3" aria-labelledby="{formId}-details">
					<h2 id="{formId}-details" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><ClipboardList size={16} class="shrink-0" aria-hidden="true" /> {t('shop.card.details')}</h2>
					<ProductFields bind:v categories={data.categories} seasons={data.seasons} teams={data.teams} fields={errs} {tried} />
				</section>
			</form>
			<section class="card card-body card-hl gap-3" aria-labelledby="{formId}-variants">
				<header class="grid gap-0.5">
					<h2 id="{formId}-variants" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Layers size={16} class="shrink-0" aria-hidden="true" /> {t('shop.card.variants')}</h2>
					<p class="text-xs text-muted">{t('shop.card.variants_desc_saved')}</p>
				</header>
				<ProductVariants productId={item.id} bind:variants price={item.price} inventoryManaged={item.inventory_managed} membership={v.product_type === 'membership'} teams={teamsForVariants} />
			</section>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body card-hl gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Settings2 size={16} class="shrink-0" aria-hidden="true" /> {t('shop.card.actions')}</h2>
				<div class="flex flex-wrap items-center gap-2 text-xs text-muted">
					<span class="badge badge-sm {item.is_active ? 'badge-success' : 'badge-warning'}">{item.is_active ? t('shop.active') : t('shop.inactive')}</span>
					{#if item.inventory_managed}<span class="badge badge-sm {item.in_stock ? 'badge-success' : 'badge-error'}">{item.in_stock ? t('shop.in_stock') : t('shop.out_of_stock')}</span>{/if}
					<span class="font-mono">{t('shop.product.order_lines', { n: item.order_line_count })}</span>
				</div>
				{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('shop.unsaved')}</p>{/if}
				<button class="btn btn-primary w-full gap-2" type="submit" form={formId} disabled={busy || !dirty}><Save size={16} /> {t('common.save')}</button>
				<ActionList><ActionRow icon={Trash2} danger disabled={busy} onclick={() => (deleteOpen = true)}>{t('common.delete')}</ActionRow></ActionList>
			</section>
			<section class="card card-body card-hl gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Images size={16} class="shrink-0" aria-hidden="true" /> {t('shop.card.image')}</h2>
				<ProductImages productId={item.id} bind:images name={item.name} />
			</section>
			<section class="card card-body card-hl gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Paperclip size={16} class="shrink-0" aria-hidden="true" /> {t('shop.card.files')}</h2>
				<ProductAttachments productId={item.id} bind:attachments />
			</section>
		</aside>
	</div>
</div>

<Modal bind:open={deleteOpen} title={t('shop.product.delete.title')}>
	<form
		method="POST"
		action="?/delete"
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			leaving = true;
			return async ({ update, result }) => {
				await update({ reset: false });
				busy = false;
				if (result.type !== 'redirect') {
					leaving = false;
					deleteOpen = false;
				}
			};
		}}>
		<p>{t('shop.product.delete.body', { name: item.name })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
