<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { History, Settings2, Ticket, ArrowLeft, Check, Copy, Power, Save, Trash2, Undo2 } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import VoucherFields from '#lib/components/shop/VoucherFields.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { dateTime, day, money, statusBadge, type VoucherIn } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.item);
	const formId = $props.id();
	const inOf = (x: typeof data.item): VoucherIn => ({ amount: x.amount, expiry_date: x.expiry_date, issued_to_id: x.issued_to?.id ?? null, notes: x.notes });
	// svelte-ignore state_referenced_locally
	let v = $state<VoucherIn>(inOf(data.item));
	$effect(() => {
		const stored = data.item;
		untrack(() => (v = inOf(stored)));
	});
	const dirty = $derived(JSON.stringify(v) !== JSON.stringify(inOf(item)));
	let tried = $state(false);
	let busy = $state(false);
	let leaving = $state(false);
	let deleteOpen = $state(false);
	let copied = $state(false);
	let useAmount = $state('');
	let useNote = $state('');

	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('shop.unsaved_confirm'))) cancel();
	});
	async function copy() {
		try {
			await navigator.clipboard.writeText(item.number);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			/* clipboard unavailable */
		}
	}

	const op = $derived(form && 'op' in form ? String(form.op) : '');
	const errs = $derived(form && op === 'save' && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const useErrs = $derived(form && op === 'consume' && 'fields' in form ? ((form.fields as Record<string, string>) ?? {}) : {});
	const blocked = $derived(!!form && 'code' in form && form.code === 'has_usage');
	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const errorText = $derived.by(() => {
		if (!form || !('status' in form) || !form.status || blocked || (op === 'consume' && Object.keys(useErrs).length) || (op === 'save' && Object.keys(errs).length)) return '';
		return form.message ? String(form.message) : form.status === 403 ? t('common.error.forbidden') : t('shop.err.failed');
	});
	const valid = () => Number(v.amount) > 0 && !!v.expiry_date;

	usePageHeader(() => ({ title: t('shop.vch.title_edit'), context: data.item.number, actions }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/shop/vouchers"><ArrowLeft size={16} /> {t('shop.vch.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('shop.vch.title_edit'))}</title></svelte:head>

<div class="grid gap-4">
	{#if data.created && !dirty}<Alert kind="success">{t('shop.vch.created')}</Alert>{/if}
	{#if done === 'save'}<Alert kind="success">{t('shop.saved')}</Alert>{/if}
	{#if done === 'activate'}<Alert kind="success">{t('shop.vch.activated')}</Alert>{/if}
	{#if done === 'deactivate'}<Alert kind="success">{t('shop.vch.deactivated')}</Alert>{/if}
	{#if done === 'consume'}<Alert kind="success">{t('shop.vch.use_recorded')}</Alert>{/if}
	{#if done === 'unconsume'}<Alert kind="success">{t('shop.vch.use_removed')}</Alert>{/if}
	{#if errorText}<Alert kind="error" title={t('shop.save_failed')}>{errorText}</Alert>{/if}
	{#if blocked}
		<Alert kind="warning" title={t('shop.vch.delete.blocked_title')}>
			<p>{t('shop.vch.delete.blocked', { number: item.number })}</p>
			{#if item.is_active}
				<form method="POST" action="?/toggle" class="mt-2" use:enhance><button class="btn btn-outline btn-sm" type="submit">{t('shop.deactivate')}</button></form>
			{/if}
		</Alert>
	{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<form
				id={formId}
				method="POST"
				action="?/save"
				class="card card-body gap-3"
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
				<VoucherFields bind:v fields={errs} {tried} holder={item.issued_to?.name ?? ''} />
			</form>

			<section class="card card-body card-hl gap-3" aria-labelledby="{formId}-hist">
				<h2 id="{formId}-hist" class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><History size={16} class="shrink-0" aria-hidden="true" /> {t('shop.vch.history')}</h2>
				{#if item.history.length}
					<div class="overflow-x-auto">
						<table class="table">
							<thead><tr><th>{t('shop.mov.when')}</th><th>{t('shop.vch.use_kind')}</th><th class="text-right">{t('shop.vch.amount')}</th><th>{t('shop.vch.use_detail')}</th><th>{t('shop.mov.by')}</th><th><span class="sr-only">{t('shop.col.actions')}</span></th></tr></thead>
							<tbody>
								{#each item.history as h, i (h.consumption_id ?? `${h.when}-${i}`)}
									<tr>
										<td class="font-mono text-xs whitespace-nowrap text-muted">{dateTime(h.when)}</td>
										<td><span class="badge badge-sm">{t(`shop.vch.kind.${h.kind}`)}</span></td>
										<td class="text-right font-mono text-sm">{money(h.amount)}</td>
										<td class="text-sm">{#if h.order_id}<a class="link" href="/shop/orders/{h.order_id}">{h.detail}</a>{:else}{h.detail}{/if}</td>
										<td class="text-sm">{h.recorded_by ?? t('common.none')}</td>
										<td class="text-right">
											{#if h.kind === 'manual' && h.consumption_id}
												<form method="POST" action="?/unconsume" use:enhance>
													<input type="hidden" name="consumption_id" value={h.consumption_id} />
													<button class="btn btn-outline btn-xs gap-1" type="submit"><Undo2 size={13} /> {t('shop.vch.use_undo')}</button>
												</form>
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{:else}
					<p class="text-sm text-muted">{t('shop.vch.history_empty')}</p>
				{/if}
			</section>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20">
			<section class="card card-body card-hl gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Settings2 size={16} class="shrink-0" aria-hidden="true" /> {t('shop.card.actions')}</h2>
				<div class="flex flex-wrap items-center gap-2">
					<span class="font-mono text-lg font-bold">{item.number}</span>
					<button class="btn btn-ghost btn-xs btn-square" type="button" aria-label={t('shop.vch.copy_named', { number: item.number })} onclick={copy}>{#if copied}<Check size={14} />{:else}<Copy size={14} />{/if}</button>
					<span class="badge badge-sm {statusBadge(item.status)}">{t(`shop.status.${item.status}`)}</span>
				</div>
				<dl class="grid grid-cols-2 gap-1 text-sm">
					<dt class="text-muted">{t('shop.vch.amount')}</dt><dd class="text-right font-mono">{money(item.amount)}</dd>
					<dt class="text-muted">{t('shop.vch.used')}</dt><dd class="text-right font-mono">{money(item.consumed_amount)}</dd>
					<dt class="text-muted">{t('shop.vch.remaining')}</dt><dd class="text-right font-mono font-bold">{money(item.available_amount)}</dd>
					<dt class="text-muted">{t('shop.vch.expiry')}</dt><dd class="text-right">{day(item.expiry_date)}</dd>
				</dl>
				{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('shop.unsaved')}</p>{/if}
				<button class="btn btn-primary w-full gap-2" type="submit" form={formId} disabled={busy || !dirty}><Save size={16} /> {t('common.save')}</button>
				<ActionList>
					<form method="POST" action="?/toggle" use:enhance>
						<input type="hidden" name="activate" value={item.is_active ? '0' : '1'} />
						<ActionRow icon={Power} type="submit" disabled={busy}>{item.is_active ? t('shop.deactivate') : t('shop.activate')}</ActionRow>
					</form>
					<ActionRow icon={Trash2} danger disabled={busy} onclick={() => (deleteOpen = true)}>{t('common.delete')}</ActionRow>
				</ActionList>
			</section>
			<section class="card card-body card-hl gap-3">
				<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase flex items-center gap-2"><Ticket size={16} class="shrink-0" aria-hidden="true" /> {t('shop.vch.use_title')}</h2>
				<p class="text-xs text-muted">{t('shop.vch.use_help')}</p>
				<form method="POST" action="?/consume" class="grid gap-3" use:enhance={() => { busy = true; return async ({ update, result }) => { await update({ reset: false }); busy = false; if (result.type === 'success') { useAmount = ''; useNote = ''; } }; }}>
					<Field label={t('shop.vch.amount')} name="amount" type="number" step="0.01" min="0.01" bind:value={useAmount} error={useErrs.amount} required />
					<Field label={t('shop.vch.use_note')} name="note" bind:value={useNote} error={useErrs.note} maxlength={255} />
					<button class="btn btn-outline" type="submit" disabled={busy || !(Number(useAmount) > 0)}>{t('shop.vch.use_record')}</button>
				</form>
			</section>
		</aside>
	</div>
</div>

<Modal bind:open={deleteOpen} title={t('shop.vch.delete.title')}>
	<form method="POST" action="?/delete" class="grid gap-3" use:enhance={() => { busy = true; leaving = true; return async ({ update, result }) => { await update({ reset: false }); busy = false; if (result.type !== 'redirect') { leaving = false; deleteOpen = false; } }; }}>
		<p>{t('shop.vch.delete.body', { number: item.number })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (deleteOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Trash2 size={16} /> {t('common.delete')}</button>
		</div>
	</form>
</Modal>
