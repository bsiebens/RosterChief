<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { ArrowLeft, Save, Trash2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import DiscountFields from '#lib/components/shop/DiscountFields.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { discountInOf, statusBadge, type DiscountIn } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.item);
	const formId = $props.id();
	// svelte-ignore state_referenced_locally
	let v = $state<DiscountIn>(discountInOf(data.item));
	$effect(() => {
		const stored = data.item;
		untrack(() => (v = discountInOf(stored)));
	});
	const dirty = $derived(JSON.stringify(v) !== JSON.stringify(discountInOf(item)));
	let tried = $state(false);
	let busy = $state(false);
	let leaving = $state(false);
	let deleteOpen = $state(false);

	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('shop.unsaved_confirm'))) cancel();
	});

	const errs = $derived(form && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const blocked = $derived(!!form && 'code' in form && form.code === 'has_orders');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const errorText = $derived.by(() => {
		if (!form || !('status' in form) || !form.status || blocked) return '';
		if (form.message) return String(form.message);
		return form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed');
	});
	const valid = () => !!v.name.trim() && !!v.code.trim() && Number(v.discount_amount) > 0 && (v.discount_type !== 'percentage' || Number(v.discount_amount) <= 100) && !(v.valid_from && v.valid_until && v.valid_until < v.valid_from);

	usePageHeader(() => ({ title: t('shop.disc.title_edit'), context: data.item.name, actions }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/discounts"><ArrowLeft size={16} /> {t('shop.disc.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.disc.title_edit'))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created && !dirty}<Alert kind="success">{t('shop.disc.created')}</Alert>{/if}
	{#if done === 'save'}<Alert kind="success">{t('shop.saved')}</Alert>{/if}
	{#if done === 'deactivate'}<Alert kind="success">{t('shop.disc.deactivated')}</Alert>{/if}
	{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}
	{#if blocked}
		<Alert kind="warning" title={t('shop.disc.delete.blocked_title')}>
			<p>{t('shop.disc.delete.blocked', { name: item.name })}</p>
			{#if item.is_active}<form method="POST" action="?/deactivate" class="mt-2" use:enhance><button class="btn btn-outline btn-sm" type="submit">{t('shop.deactivate')}</button></form>{/if}
		</Alert>
	{/if}
	<div class="grid items-start gap-5 xl:grid-cols-3">
		<form
			id={formId}
			method="POST"
			action="?/save"
			class="card card-body gap-3 xl:col-span-2"
			novalidate
			use:enhance={({ cancel }) => {
				if (!valid()) {
					tried = true;
					cancel();
					return;
				}
				busy = true;
				leaving = true;
				return async ({ update }) => {
					await update({ reset: false });
					busy = false;
					leaving = false;
					tried = false;
				};
			}}>
			<input type="hidden" name="payload" value={JSON.stringify(v)} />
			<DiscountFields bind:v fields={errs} {tried} />
		</form>
		<aside class="card card-body gap-3 xl:sticky xl:top-20">
			<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('shop.card.actions')}</h2>
			<div class="flex flex-wrap items-center gap-2 text-xs text-muted">
				<span class="badge badge-sm {statusBadge(item.status)}">{t(`shop.status.${item.status}`)}</span>
				<span class="font-mono">{t('shop.disc.used_n', { n: item.used_count })}</span>
			</div>
			{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('shop.unsaved')}</p>{/if}
			<button class="btn btn-primary w-full gap-2" type="submit" form={formId} disabled={busy || !dirty}><Save size={16} /> {t('common.save')}</button>
			<ActionList><ActionRow icon={Trash2} danger disabled={busy} onclick={() => (deleteOpen = true)}>{t('common.delete')}</ActionRow></ActionList>
		</aside>
	</div>
</div>

<Modal bind:open={deleteOpen} title={t('shop.disc.delete.title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={() => { busy = true; leaving = true; return async ({ update, result }) => { await update({ reset: false }); busy = false; if (result.type !== 'redirect') { leaving = false; deleteOpen = false; } }; }}>
		<p>{t('shop.disc.delete.body', { name: item.name })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
