<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { Banknote } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import { formatDay } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { PAYMENT_METHODS, type Installment, type PaymentIn, type PaymentMethod, type VoucherLookup } from '#lib/registrations.ts';
	import { money } from '#lib/shop.ts';

	/** Record a payment against the invoice: all of it or part of it, for everyone (the amount fills the people in order) or per person, in cash,
	 * by transfer or card, or with a shop voucher. `memberId` opens it for one person. The page's `?/pay` action does the call. */
	/** The people and balances to pay: a registration's lines, or one member's open fee (`lines` with a single entry). */
	type Payable = { lines: { membership_id: string; member: { name: string }; balance: string; is_credit?: boolean; excluded?: boolean; next_installment?: Installment | null }[]; vouchers_enabled: boolean; next_installment?: Installment | null };
	let { open = $bindable(false), item, membershipId = null, error = '', fields = {}, action = '?/pay' }: { open: boolean; item: Payable; membershipId?: string | null; error?: string; fields?: Record<string, string>; /** The page's form action that records it (reads the hidden `payload`, and `membership_id` for a single member). */ action?: string } = $props();

	const id = $props.id();
	const num = (v: unknown) => {
		const n = Number(v ?? 0);
		return Number.isFinite(n) ? n : 0;
	};
	const cents = (n: number) => Math.round(n * 100) / 100;
	const people = $derived(item.lines.filter((l) => !l.is_credit && !l.excluded && num(l.balance) > 0));
	const balance = $derived(cents(item.lines.reduce((sum, l) => sum + num(l.balance), 0)));
	const methods = $derived(PAYMENT_METHODS.filter((m) => m !== 'voucher' || item.vouchers_enabled));

	let busy = $state(false);
	let amount = $state('');
	let method = $state<PaymentMethod>('bank_transfer');
	let reference = $state('');
	let paidAt = $state('');
	let note = $state('');
	let voucher = $state('');
	let voucherInfo = $state<VoucherLookup | 'unknown' | null>(null);
	let split = $state(false);
	let parts = $state<Record<string, string>>({});

	/** The amount spread over the people in order, each up to what they owe. */
	const spread = (total: number): Record<string, string> => {
		let left = total;
		const out: Record<string, string> = {};
		for (const l of people) {
			const take = Math.min(num(l.balance), left);
			out[l.membership_id] = take > 0 ? String(cents(take)) : '';
			left = cents(left - take);
		}
		return out;
	};
	const sum = $derived(cents(Object.values(parts).reduce((s, v) => s + num(v), 0)));
	const total = $derived(split ? sum : num(amount));

	// Every time the dialog opens it starts afresh: everything that is open, or the one person's share.
	$effect(() => {
		if (!open) return;
		untrack(() => {
			method = 'bank_transfer';
			reference = paidAt = note = voucher = '';
			voucherInfo = null;
			const one = membershipId ? item.lines.find((l) => l.membership_id === membershipId) : null;
			if (one) {
				const suggested = one.next_installment ? Math.min(num(one.next_installment.remaining), num(one.balance)) : num(one.balance);
				split = true;
				parts = { [one.membership_id]: String(suggested) };
				amount = String(suggested);
			} else {
				split = false;
				parts = {};
				amount = String(item.next_installment ? Math.min(num(item.next_installment.remaining), balance) : balance);
			}
		});
	});

	const toggleSplit = (on: boolean) => {
		split = on;
		if (on) parts = spread(num(amount));
		else amount = String(sum || balance);
	};
	const lookup = async () => {
		voucherInfo = null;
		if (!voucher.trim()) return;
		const r = await fetch(`/api/v1/registrations/vouchers/lookup?number=${encodeURIComponent(voucher.trim())}`, { headers: { accept: 'application/json' } }).catch(() => null);
		voucherInfo = r?.ok ? ((await r.json()) as VoucherLookup) : 'unknown';
	};
	const payload = $derived.by(() => {
		const body: PaymentIn = { amount: total.toFixed(2), method, reference: reference.trim(), note: note.trim(), paid_at: paidAt ? new Date(paidAt).toISOString() : null };
		if (method === 'voucher') body.voucher_number = voucher.trim();
		if (split) body.allocations = Object.entries(parts).filter(([, v]) => num(v) > 0).map(([membership_id, v]) => ({ membership_id, amount: num(v).toFixed(2) }));
		return JSON.stringify(body);
	});
	const owes = (l: (typeof people)[number]) => t('fin.dlg.split_owes', { balance: money(l.balance) });
	const tooMuch = $derived(total > balance + 0.004);
	const voucherShort = $derived(method === 'voucher' && voucherInfo && voucherInfo !== 'unknown' && num(voucherInfo.available) < total);
	const canSubmit = $derived(!busy && total > 0 && !tooMuch && (method !== 'voucher' || (voucher.trim() !== '' && voucherInfo !== 'unknown' && !voucherShort)));
	const title = $derived.by(() => {
		const one = membershipId ? item.lines.find((l) => l.membership_id === membershipId) : null;
		return one ? t('fin.pay_for', { name: one.member.name }) : t('fin.dlg.title');
	});
</script>

<Modal bind:open {title}>
	<form
		method="POST"
		{action}
		class="grid gap-3"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				await update({ reset: false });
				busy = false;
				if (result.type === 'success') open = false;
			};
		}}
	>
		{#if error}<Alert kind="error" title={t('fin.dlg.failed')}>{error}</Alert>{/if}
		<input type="hidden" name="payload" value={payload} />
		{#if action !== '?/pay' && item.lines[0]}<input type="hidden" name="membership_id" value={item.lines[0].membership_id} />{/if}

		<div class="form-control w-full">
			<label class="label" for="{id}-amount"><span class="label-text">{t('fin.dlg.amount')}</span></label>
			<div class="flex items-center gap-2">
				<input id="{id}-amount" class="input input-bordered w-full font-mono {fields.amount ? 'input-error' : ''}" type="number" min="0.01" step="0.01" inputmode="decimal" required={!split} disabled={split} value={split ? String(sum) : amount} oninput={(e) => (amount = e.currentTarget.value)} />
				<button class="btn btn-outline whitespace-nowrap" type="button" disabled={split} onclick={() => (amount = String(balance))}>{t('fin.dlg.full')}</button>
			</div>
			{#if fields.amount}<span class="mt-1 block text-xs text-error">{fields.amount}</span>
			{:else}<span class="label-text-alt mt-1 block {tooMuch ? 'text-error' : ''}">{t('fin.dlg.amount_help', { balance: money(balance) })}</span>{/if}
		</div>

		{#if people.length > 1 || split}
			<label class="flex items-start gap-3 text-sm"><input type="checkbox" class="checkbox checkbox-sm mt-0.5" checked={split} disabled={!!membershipId} onchange={(e) => toggleSplit(e.currentTarget.checked)} /> <span>{t('fin.dlg.split')}<span class="block text-xs text-muted">{t(item.next_installment ? 'installments.split_help' : 'fin.dlg.split_help')}</span></span></label>
			{#if split}
				<div class="grid gap-2 rounded-lg border border-rule p-3">
					{#each people as l (l.membership_id)}
						<div class="grid grid-cols-[1fr_8rem] items-center gap-3">
							<label for="{id}-p-{l.membership_id}" class="text-sm"><span class="font-semibold text-ink">{l.member.name}</span> <span class="text-xs text-muted">{owes(l)}</span></label>
							<input id="{id}-p-{l.membership_id}" class="input input-bordered input-sm w-full text-right font-mono" type="number" min="0" max={num(l.balance)} step="0.01" inputmode="decimal" placeholder="0.00" bind:value={parts[l.membership_id]} />
						</div>
					{/each}
					<p class="text-xs text-muted">{t('fin.dlg.split_sum', { sum: money(sum), amount: money(total) })}</p>
					{#if fields.allocations}<p class="text-xs text-error">{fields.allocations}</p>{/if}
				</div>
			{/if}
		{/if}

		<div class="form-control w-full">
			<label class="label" for="{id}-method"><span class="label-text">{t('fin.dlg.method')}</span></label>
			<select id="{id}-method" class="select select-bordered w-full" bind:value={method} onchange={() => (voucherInfo = null)}>
				{#each methods as m (m)}<option value={m}>{t(`fin.method.${m}`)}</option>{/each}
			</select>
			{#if fields.method}<span class="mt-1 block text-xs text-error">{fields.method}</span>{/if}
		</div>

		{#if method === 'voucher'}
			<div class="form-control w-full">
				<label class="label" for="{id}-voucher"><span class="label-text">{t('fin.dlg.voucher')}</span></label>
				<div class="flex items-center gap-2">
					<input id="{id}-voucher" class="input input-bordered w-full font-mono {fields.voucher_number ? 'input-error' : ''}" autocomplete="off" bind:value={voucher} oninput={() => (voucherInfo = null)} onblur={lookup} />
					<button class="btn btn-outline" type="button" onclick={lookup}>{t('fin.dlg.voucher_check')}</button>
				</div>
				{#if fields.voucher_number}<span class="mt-1 block text-xs text-error">{fields.voucher_number}</span>
				{:else if voucherInfo === 'unknown'}<span class="mt-1 block text-xs text-error">{t('fin.dlg.voucher_unknown')}</span>
				{:else if voucherInfo && voucherInfo.status !== 'usable'}<span class="mt-1 block text-xs text-error">{t('fin.dlg.voucher_unusable', { number: voucherInfo.number, status: t(`fin.dlg.voucher_status.${voucherInfo.status}`), available: money(voucherInfo.available) })}</span>
				{:else if voucherInfo}<span class="mt-1 block text-xs {voucherShort ? 'text-error' : 'text-ok-text'}">{t('fin.dlg.voucher_ok', { number: voucherInfo.number, available: money(voucherInfo.available), date: formatDay(voucherInfo.expiry_date) })}</span>{/if}
			</div>
		{/if}

		<div class="form-control w-full">
			<label class="label" for="{id}-ref"><span class="label-text">{t('fin.dlg.reference')}</span></label>
			<input id="{id}-ref" class="input input-bordered w-full" autocomplete="off" bind:value={reference} />
			<span class="label-text-alt mt-1 block">{t('fin.dlg.reference_help')}</span>
		</div>
		<div class="form-control w-full">
			<label class="label" for="{id}-when"><span class="label-text">{t('fin.dlg.paid_at')}</span></label>
			<input id="{id}-when" class="input input-bordered w-full" type="datetime-local" bind:value={paidAt} />
			<span class="label-text-alt mt-1 block {fields.paid_at ? 'text-error' : ''}">{fields.paid_at || t('fin.dlg.paid_at_help')}</span>
		</div>
		<div class="form-control w-full">
			<label class="label" for="{id}-note"><span class="label-text">{t('fin.dlg.note')}</span></label>
			<input id="{id}-note" class="input input-bordered w-full" autocomplete="off" bind:value={note} />
		</div>

		<div class="modal-action mt-2 flex justify-end gap-2">
			<button class="btn btn-outline" type="button" onclick={() => (open = false)}>{t('common.cancel')}</button>
			<button class="btn btn-primary gap-2" type="submit" disabled={!canSubmit}><Banknote size={16} /> {t('fin.dlg.submit')}</button>
		</div>
	</form>
</Modal>
