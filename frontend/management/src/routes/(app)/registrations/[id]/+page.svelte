<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import { ArrowLeft, Ban, ExternalLink, FileDown, Mail, MailCheck, ReceiptText, Save, Send } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import ActionList from '#lib/components/actions/ActionList.svelte';
	import ActionRow from '#lib/components/actions/ActionRow.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { formatDay } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { invoicePdfHref, statusBadge, type RegistrationDetail, type ReviewIn } from '#lib/registrations.ts';
	import { money } from '#lib/shop.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data, form } = $props();
	const item = $derived(data.item);
	const editable = $derived(item.status === 'awaiting');
	const formId = $props.id();

	type Draft = { lines: Record<string, { override: string; excluded: boolean; team: string }>; discount: string; note: string };
	const draftOf = (r: RegistrationDetail): Draft => ({
		lines: Object.fromEntries(r.lines.map((l) => [l.id, { override: l.price_override === null ? '' : String(Number(l.price_override)), excluded: l.excluded, team: l.requested_team_id ?? '' }])),
		discount: Number(r.manual_discount_amount) ? String(Number(r.manual_discount_amount)) : '',
		note: r.manual_discount_note
	});
	// svelte-ignore state_referenced_locally
	let d = $state<Draft>(draftOf(data.item));
	// A reload after a save brings the stored review: start the editor from it again.
	$effect(() => {
		const stored = data.item;
		untrack(() => (d = draftOf(stored)));
	});
	const dirty = $derived(JSON.stringify(d) !== JSON.stringify(draftOf(item)));

	/** The line while editing, before the credits: the override, else base minus the registrant discount; nothing when excluded. A credit line (negative
	 * base price) is its negative amount: it is never overridden. */
	const lineBefore = (id: string) => {
		const l = item.lines.find((x) => x.id === id)!;
		const e = d.lines[id];
		if (e.excluded) return 0;
		if (l.is_credit) return Number(l.base_price);
		return e.override !== '' ? Number(e.override) : Math.max(0, Number(l.base_price) - Number(l.discount_amount));
	};
	/** The credits (as a positive amount) and how much of that the other lines can take (never more than they cost). */
	const creditTotal = $derived(item.lines.filter((l) => l.is_credit).reduce((s, l) => s - lineBefore(l.id), 0));
	const costs = $derived(item.lines.filter((l) => !l.is_credit).reduce((s, l) => s + lineBefore(l.id), 0));
	const creditApplied = $derived(Math.min(creditTotal, costs));
	/** A line's share of the credit (in proportion to what it costs; the server settles the cents). */
	const creditShare = (id: string) => (costs > 0 ? Math.round(((creditApplied * lineBefore(id)) / costs) * 100) / 100 : 0);
	/** What the person pays before the manual discount: nothing for a credit line. */
	const linePrice = (id: string) => (item.lines.find((x) => x.id === id)!.is_credit ? 0 : Math.max(0, lineBefore(id) - creditShare(id)));
	const subtotal = $derived(item.lines.reduce((s, l) => s + linePrice(l.id), 0));
	const manual = $derived(Math.min(Math.max(0, Number(d.discount) || 0), subtotal));
	const total = $derived(subtotal - manual);

	const payload = $derived(
		JSON.stringify({
			lines: item.lines.map((l) => ({ id: l.id, price: d.lines[l.id].override === '' ? null : Number(d.lines[l.id].override), excluded: d.lines[l.id].excluded, ...(l.entry_kind === 'player' ? { requested_team_id: d.lines[l.id].team || null } : {}) })),
			manual_discount_amount: Number(d.discount) || 0,
			manual_discount_note: d.note.trim()
		} satisfies ReviewIn)
	);

	let busy = $state(false);
	let leaving = $state(false);
	let confirmOpen = $state(false);
	let cancelOpen = $state(false);
	const run = (close?: () => void) => () => {
		busy = true;
		return async ({ update, result }: { update: (o?: { reset?: boolean }) => Promise<void>; result: { type: string } }) => {
			await update({ reset: false });
			busy = false;
			if (result.type === 'success') close?.();
		};
	};
	beforeNavigate(({ cancel, willUnload, from, to }) => {
		if (!dirty || leaving || busy) return;
		if (to && from && to.url.pathname === from.url.pathname && !willUnload) return;
		if (willUnload) cancel();
		else if (!confirm(t('reg.unsaved_confirm'))) cancel();
	});

	const done = $derived(form && 'done' in form ? String(form.done) : '');
	const sentOk = $derived(form && 'sent' in form ? !!form.sent : true);
	const failure = $derived.by(() => {
		if (!form || 'done' in form || !('op' in form)) return '';
		const code = String(form.code ?? '');
		if (['confirmed', 'cancelled', 'not_confirmed', 'no_pdf', 'pdf_unavailable'].includes(code)) return t(`reg.err.${code}`);
		if (form.status === 503) return t('reg.err.pdf_unavailable');
		return String(form.message || t('common.error.generic'));
	});
	const errs = $derived((form && 'fields' in form ? form.fields : {}) as Record<string, string>);
	const publicUrl = $derived(`${data.origin}${item.status_path}`);

	usePageHeader(() => ({ title: t('reg.detail.title'), context: item.contact?.name ?? '', actions }));
</script>

<svelte:window onbeforeunload={(e) => dirty && !leaving && e.preventDefault()} />

{#snippet actions()}
	<a class="btn btn-outline gap-2" href="/registrations"><ArrowLeft size={16} /> {t('reg.back')}</a>
{/snippet}

<svelte:head><title>{pageTitle(t('reg.detail.title'))}</title></svelte:head>

<div class="grid gap-4">
	{#if done === 'save'}<Alert kind="success">{t('reg.saved')}</Alert>{/if}
	{#if done === 'confirm'}<Alert kind="success">{t('reg.confirmed_ok')}</Alert>{/if}
	{#if done === 'cancel'}<Alert kind="success">{t('reg.cancelled_ok')}</Alert>{/if}
	{#if done === 'resendConfirmation' || done === 'resendInvoice'}<Alert kind={sentOk ? 'success' : 'warning'}>{sentOk ? t('reg.sent_ok') : t('reg.sent_failed')}</Alert>{/if}
	{#if failure}<Alert kind="error" title={t('reg.action_failed')}>{failure}</Alert>{/if}
	{#if item.status === 'confirmed'}<Alert kind="info">{t('reg.readonly_confirmed', { number: item.invoice_number ?? '' })}</Alert>{/if}
	{#if item.status === 'cancelled'}<Alert kind="warning">{t('reg.readonly_cancelled')}</Alert>{/if}

	<div class="grid items-start gap-5 xl:grid-cols-3">
		<div class="grid content-start gap-5 xl:col-span-2">
			<form id={formId} method="POST" action="?/save" class="grid gap-5" use:enhance={run()}>
				<input type="hidden" name="payload" value={payload} />
				<section class="card card-body gap-3">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('reg.card.lines')}</h2>
					<p class="text-xs text-muted">{t('reg.card.lines_help')}</p>
					<div class="overflow-x-auto">
						<table class="table">
							<thead>
								<tr>
									<th>{t('reg.line.person')}</th>
									<th>{t('reg.line.requested')}</th>
									<th class="text-right">{t('reg.line.base')}</th>
									<th class="text-right">{t('reg.line.discount')}</th>
									<th class="w-32 text-right">{t('reg.line.override')}</th>
									<th class="text-center">{t('reg.line.exclude')}</th>
									<th class="text-right">{t('reg.line.price')}</th>
								</tr>
							</thead>
							<tbody>
								{#each item.lines as l (l.id)}
									{@const e = d.lines[l.id]}
									<tr class={e.excluded ? 'opacity-60' : ''}>
										<td>
											<a class="link font-semibold" href="/members/{l.member.id}">{l.member.name}</a>
											<p class="text-xs text-muted">{l.description} · {t(`reg.kind.${l.entry_kind}`)}</p>
										</td>
										<td class="text-sm">
											{#if editable && l.entry_kind === 'player'}
												<select class="select select-bordered select-sm w-44 max-w-full" aria-label={t('reg.line.team_named', { name: l.member.name })} bind:value={e.team}>
													<option value="">{t('reg.line.no_team')}</option>
													{#each data.teams as team (team.id)}<option value={team.id}>{team.name}</option>{/each}
												</select>
												{#if l.requested_position_name || l.requested_jersey_number !== null}<p class="mt-1 text-xs text-muted">{[l.requested_position_name, l.requested_jersey_number !== null ? `#${l.requested_jersey_number}` : ''].filter(Boolean).join(' · ')}</p>{/if}
											{:else if l.requested_team_name || l.requested_position_name || l.requested_jersey_number !== null}
												{[l.requested_team_name, l.requested_position_name, l.requested_jersey_number !== null ? `#${l.requested_jersey_number}` : ''].filter(Boolean).join(' · ')}
											{:else}<span class="text-muted">–</span>{/if}
										</td>
										<td class="text-right font-mono text-sm {l.is_credit ? 'text-ok-text' : ''}">{money(l.base_price)}</td>
										<td class="text-right font-mono text-sm">{Number(l.discount_amount) ? `−${money(l.discount_amount)}` : '–'}</td>
										<td class="text-right">
											<input class="input input-bordered input-sm w-28 text-right font-mono" type="number" step="0.01" min="0" inputmode="decimal" aria-label={t('reg.line.override_named', { name: l.member.name })} placeholder="–" bind:value={e.override} disabled={!editable || e.excluded || l.is_credit} />
										</td>
										<td class="text-center"><input type="checkbox" class="checkbox checkbox-sm" aria-label={t('reg.line.exclude_named', { name: l.member.name })} bind:checked={e.excluded} disabled={!editable} /></td>
										<td class="text-right font-mono text-sm font-semibold">
											{#if l.is_credit}
												<span class="badge badge-sm badge-success mr-1">{t('regr.credit')}</span><span class="text-ok-text">{money(editable ? lineBefore(l.id) : l.price)}</span>
											{:else}
												{money(editable ? linePrice(l.id) : l.payable)}
												{#if (editable ? creditShare(l.id) : Number(l.credit_share)) > 0}<span class="block text-xs font-normal text-ok-text">{t('regr.credit_taken', { amount: money(editable ? creditShare(l.id) : l.credit_share) })}</span>{/if}
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>

				<section class="card card-body gap-3">
					<h2 class="font-display text-sm font-extrabold tracking-[.08em] text-ink uppercase">{t('reg.card.discount')}</h2>
					<div class="grid gap-3 md:grid-cols-[12rem_1fr]">
						<div class="form-control">
							<label class="label" for="{formId}-disc"><span class="label-text">{t('reg.discount.amount')}</span></label>
							<input id="{formId}-disc" class="input input-bordered w-full font-mono {errs.manual_discount_amount ? 'input-error' : ''}" type="number" step="0.01" min="0" inputmode="decimal" bind:value={d.discount} disabled={!editable} />
							{#if errs.manual_discount_amount}<span class="mt-1 block text-xs text-error">{errs.manual_discount_amount}</span>{/if}
						</div>
						<div class="form-control">
							<label class="label" for="{formId}-note"><span class="label-text">{t('reg.discount.note')}</span></label>
							<input id="{formId}-note" class="input input-bordered w-full" maxlength="255" bind:value={d.note} disabled={!editable} />
							<span class="label-text-alt mt-1 block {errs.manual_discount_note ? 'text-error' : ''}">{errs.manual_discount_note || t('reg.discount.note_help')}</span>
						</div>
					</div>
				</section>
			</form>
		</div>

		<aside class="grid content-start gap-5 xl:sticky xl:top-20" aria-label={t('reg.card.summary')}>
			<section class="card card-body gap-3">
				<div class="flex flex-wrap items-center gap-2">
					<span class="badge badge-sm {statusBadge(item.status)}">{t(`reg.status.${item.status}`)}</span>
					{#if item.invoice_number}<span class="font-mono text-xs text-muted">{item.invoice_number}</span>{/if}
				</div>
				<dl class="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
					<dt class="text-muted">{t('reg.sum.contact')}</dt><dd class="text-right">{item.contact?.name ?? '–'}</dd>
					{#if item.contact?.email}<dt class="text-muted">{t('reg.sum.email')}</dt><dd class="truncate text-right">{item.contact.email}</dd>{/if}
					<dt class="text-muted">{t('reg.sum.season')}</dt><dd class="text-right">{item.season_name}</dd>
					<dt class="text-muted">{t('reg.sum.submitted')}</dt><dd class="text-right">{formatDay(item.submitted_at)}</dd>
					{#if item.invoice_due_date}<dt class="text-muted">{t('reg.sum.due')}</dt><dd class="text-right">{formatDay(item.invoice_due_date)}</dd>{/if}
					{#if (editable ? creditTotal : Number(item.credit_total)) > 0}<dt class="text-muted">{t('regr.sum.credit')}</dt><dd class="text-right font-mono text-ok-text">−{money(editable ? creditApplied : item.credit_applied)}</dd>{/if}
					{#if (editable ? creditTotal - creditApplied : Number(item.credit_total) - Number(item.credit_applied)) > 0}<dt class="text-xs text-muted">{t('regr.sum.credit_unused')}</dt><dd class="text-right font-mono text-xs text-muted">{money(editable ? creditTotal - creditApplied : Number(item.credit_total) - Number(item.credit_applied))}</dd>{/if}
					<dt class="text-muted">{t('reg.sum.subtotal')}</dt><dd class="text-right font-mono">{money(editable ? subtotal : item.subtotal)}</dd>
					{#if (editable ? manual : Number(item.manual_discount_amount)) > 0}<dt class="text-muted">{t('reg.sum.manual')}</dt><dd class="text-right font-mono">−{money(editable ? manual : item.manual_discount_amount)}</dd>{/if}
				</dl>
				<div class="flex items-baseline justify-between border-t border-rule pt-2"><span class="font-display text-xs font-bold tracking-[.08em] text-muted uppercase">{t('reg.sum.total')}</span><span class="font-display text-2xl font-extrabold text-ink tabular-nums">{money(editable ? total : item.total)}</span></div>
				{#if dirty}<p class="text-xs font-semibold text-warn-text">{t('reg.unsaved')}</p>{/if}
				{#if editable}
					<div class="grid gap-2">
						<button class="btn btn-outline w-full gap-2" type="submit" form={formId} disabled={busy || !dirty}><Save size={16} /> {t('reg.save')}</button>
						<button class="btn btn-primary w-full gap-2" type="button" disabled={busy || dirty} title={dirty ? t('reg.confirm_save_first') : ''} onclick={() => (confirmOpen = true)}><ReceiptText size={16} /> {t('reg.confirm')}</button>
						{#if dirty}<p class="text-xs text-muted">{t('reg.confirm_save_first')}</p>{/if}
					</div>
				{/if}
				<ActionList>
					{#if item.status === 'confirmed'}<ActionRow icon={FileDown} chevron href={invoicePdfHref(item.id)}>{t('reg.act.pdf')}</ActionRow>{/if}
					<form method="POST" action="?/resendConfirmation" use:enhance={run()}><ActionRow icon={Mail} type="submit" disabled={busy || item.status === 'cancelled'}>{t('reg.act.resend_confirmation')}</ActionRow></form>
					{#if item.status === 'confirmed'}<form method="POST" action="?/resendInvoice" use:enhance={run()}><ActionRow icon={MailCheck} type="submit" disabled={busy}>{t('reg.act.resend_invoice')}</ActionRow></form>{/if}
					<ActionRow icon={ExternalLink} chevron href={publicUrl}>{t('reg.act.status_page')}</ActionRow>
					{#if item.status !== 'cancelled'}<ActionRow icon={Ban} danger disabled={busy} onclick={() => (cancelOpen = true)}>{t('reg.act.cancel')}</ActionRow>{/if}
				</ActionList>
			</section>
		</aside>
	</div>
</div>

<Modal bind:open={confirmOpen} title={t('reg.confirm_title')}>
	<form method="POST" action="?/confirm" class="grid gap-4" use:enhance={run(() => (confirmOpen = false))}>
		<p class="text-sm text-muted">{t('reg.confirm_body', { total: money(total) })}</p>
		<div class="form-control">
			<label class="label" for="{formId}-due"><span class="label-text">{t('reg.confirm_due')}</span></label>
			<input id="{formId}-due" class="input input-bordered w-full" type="date" name="due_date" value={item.invoice_due_date ?? ''} />
			<span class="label-text-alt mt-1 block">{t('reg.confirm_due_help')}</span>
		</div>
		<label class="flex items-start gap-3 text-sm"><input type="checkbox" name="send" class="checkbox checkbox-sm mt-0.5" checked /> <span>{t('reg.confirm_send')}</span></label>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (confirmOpen = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={busy}><Send size={16} /> {t('reg.confirm')}</button>
		</div>
	</form>
</Modal>

<Modal bind:open={cancelOpen} title={t('reg.cancel_title')}>
	<form method="POST" action="?/cancel" class="grid gap-3" use:enhance={run(() => (cancelOpen = false))}>
		<p>{t('reg.cancel_body', { name: item.contact?.name ?? '' })}</p>
		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (cancelOpen = false)}>{t('reg.keep')}</button>
			<button class="btn btn-error gap-2" type="submit" disabled={busy}><Ban size={16} /> {t('reg.cancel_confirm')}</button>
		</div>
	</form>
</Modal>
