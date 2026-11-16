import { error, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import { failed, shopInvoicesOn, shopOrdersOn, type AvailableVoucher, type InvoiceItem, type OrderDetail, type Payment } from '#lib/shop-orders.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOrdersOn(me)) redirect(303, '/');
	const res = await api<OrderDetail>('GET', `/shop/orders/${params.id}`, { f: fetch });
	if (!res.ok) error(res.status >= 400 && res.status < 600 ? res.status : 500, res.message);
	// The vouchers the purchaser (or their family) can still use, for the voucher picker while something is due.
	const vouchers = res.data!.can_apply_voucher ? await api<AvailableVoucher[]>('GET', `/shop/orders/${params.id}/vouchers`, { f: fetch }) : null;
	return { vouchers: vouchers?.ok ? (vouchers.data ?? []) : [], order: res.data!, created: url.searchParams.get('created') === '1', canInvoice: shopInvoicesOn(me) };
};

const str = (f: FormData, k: string) => String(f.get(k) ?? '').trim();
/** Only the fields that were sent, so the API keeps the rest. */
const pick = (f: FormData, keys: string[]) => Object.fromEntries(keys.filter((k) => f.has(k)).map((k) => [k, str(f, k)]));

export const actions: Actions = {
	ready: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const res = await api<OrderDetail>('POST', `/shop/orders/${params.id}/ready`, { body: { pickup_instructions: str(f, 'pickup_instructions') }, f: fetch });
		return res.ok ? { done: 'ready' } : failed('ready', res);
	},
	deliver: async ({ params, fetch }) => {
		const res = await api<OrderDetail>('POST', `/shop/orders/${params.id}/deliver`, { f: fetch });
		return res.ok ? { done: 'deliver' } : failed('deliver', res);
	},
	reopen: async ({ params, fetch }) => {
		const res = await api<OrderDetail>('POST', `/shop/orders/${params.id}/reopen`, { f: fetch });
		return res.ok ? { done: 'reopen' } : failed('reopen', res);
	},
	cancel: async ({ request, params, fetch }) => {
		const f = await request.formData();
		// Only what was ticked is sent, so a plain cancel stays a plain POST.
		const body = { ...(str(f, 'override') === 'true' ? { override: true } : {}), ...(str(f, 'refund_vouchers') === 'true' ? { refund_vouchers: true } : {}) };
		const res = await api<OrderDetail>('POST', `/shop/orders/${params.id}/cancel`, { ...(Object.keys(body).length ? { body } : {}), f: fetch });
		return res.ok ? { done: 'cancel' } : failed('cancel', res);
	},
	markPaid: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const res = await api<OrderDetail>('POST', `/shop/orders/${params.id}/mark-paid`, { body: { method: str(f, 'method') || 'cash', reference: str(f, 'reference') }, f: fetch });
		return res.ok ? { done: 'markPaid' } : failed('markPaid', res);
	},
	saveNotes: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const res = await api<OrderDetail>('PATCH', `/shop/orders/${params.id}`, { body: pick(f, ['notes', 'pickup_instructions']), f: fetch });
		return res.ok ? { done: 'saveNotes' } : failed('saveNotes', res);
	},
	addPayment: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const method = str(f, 'method');
		const body: Record<string, unknown> = { amount: str(f, 'amount'), method, reference: str(f, 'reference'), note: str(f, 'note') };
		if (method === 'voucher') body.voucher_number = str(f, 'voucher_number');
		if (str(f, 'paid_at')) body.paid_at = new Date(str(f, 'paid_at')).toISOString();
		const res = await api<Payment>('POST', `/shop/orders/${params.id}/payments`, { body, f: fetch });
		return res.ok ? { done: 'addPayment' } : failed('addPayment', res);
	},
	editPayment: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const id = str(f, 'id');
		const body: Record<string, unknown> = pick(f, ['amount', 'method', 'reference', 'note']);
		if (f.has('paid_at')) body.paid_at = str(f, 'paid_at') ? new Date(str(f, 'paid_at')).toISOString() : null;
		const res = await api<Payment>('PATCH', `/shop/orders/${params.id}/payments/${id}`, { body, f: fetch });
		return res.ok ? { done: 'editPayment' } : failed('editPayment', res, { id });
	},
	deletePayment: async ({ request, params, fetch }) => {
		const id = str(await request.formData(), 'id');
		const res: ApiCall<unknown> = await api('DELETE', `/shop/orders/${params.id}/payments/${id}`, { f: fetch });
		return res.ok ? { done: 'deletePayment' } : failed('deletePayment', res);
	},
	refundPayment: async ({ request, params, fetch }) => {
		const id = str(await request.formData(), 'id');
		const res = await api<Payment>('POST', `/shop/orders/${params.id}/payments/${id}/refund`, { f: fetch });
		return res.ok ? { done: 'refundPayment' } : failed('refundPayment', res);
	},
	payLink: async ({ request, params, url, fetch }) => {
		const f = await request.formData();
		const body: Record<string, unknown> = { return_url: `${url.origin}/` };
		if (str(f, 'amount')) body.amount = str(f, 'amount');
		const res = await api<Payment>('POST', `/shop/orders/${params.id}/payment-link`, { body, f: fetch });
		return res.ok ? { done: 'payLink', link: res.data?.payment_url ?? '' } : failed('payLink', res);
	},
	applyDiscount: async ({ request, params, fetch }) => {
		const res = await api<OrderDetail>('POST', `/shop/orders/${params.id}/discount`, { body: { code: str(await request.formData(), 'code') }, f: fetch });
		return res.ok ? { done: 'applyDiscount' } : failed('applyDiscount', res);
	},
	removeDiscount: async ({ request, params, fetch }) => {
		const id = str(await request.formData(), 'id');
		const res = await api<OrderDetail>('DELETE', `/shop/orders/${params.id}/discounts/${id}`, { f: fetch });
		return res.ok ? { done: 'removeDiscount' } : failed('removeDiscount', res);
	},
	applyVoucher: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const body: Record<string, string> = { number: str(f, 'number') };
		if (str(f, 'amount')) body.amount = str(f, 'amount');
		const res = await api<OrderDetail>('POST', `/shop/orders/${params.id}/voucher`, { body, f: fetch });
		return res.ok ? { done: 'applyVoucher' } : failed('applyVoucher', res);
	},
	createInvoice: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const res = await api<InvoiceItem>('POST', `/shop/orders/${params.id}/invoice`, { body: str(f, 'due_date') ? { due_date: str(f, 'due_date') } : {}, f: fetch });
		return res.ok ? { done: 'createInvoice' } : failed('createInvoice', res);
	}
};
