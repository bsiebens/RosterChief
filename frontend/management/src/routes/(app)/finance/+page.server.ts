import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { PAYMENT_TABS, type FinanceSummary, type OpenMembers, type PaymentIn, type PaymentTab, type RegistrationList } from '#lib/registrations.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_manage_finance) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const asked = url.searchParams.get('payment') as PaymentTab;
	const payment: PaymentTab = PAYMENT_TABS.includes(asked) ? asked : 'open';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const [res, open, members, summary] = await Promise.all([
		api<RegistrationList>('GET', '/registrations', { query: { status: 'confirmed', payment: payment === 'all' ? undefined : payment, q, limit: PAGE_SIZE, offset: offset || undefined }, f: fetch }),
		api<RegistrationList>('GET', '/registrations', { query: { status: 'confirmed', payment: 'open', limit: 1 }, f: fetch }),
		// Members with a fee still open that did not come in through a registration (import, added by hand).
		api<OpenMembers>('GET', '/finance/open-members', { query: { q, limit: 100 }, f: fetch }),
		// Totals of all confirmed invoices; the row hides when this fails.
		api<FinanceSummary>('GET', '/finance/summary', { f: fetch })
	]);
	return { totals: summary.ok ? (summary.data ?? null) : null, openMembers: members.data?.results ?? [], openMembersCount: members.data?.count ?? 0, vouchersEnabled: !!members.data?.vouchers_enabled, filters: { q, payment, offset }, rows: res.data?.results ?? [], count: res.data?.count ?? 0, openCount: open.data?.count ?? 0, pageSize: PAGE_SIZE, error: res.ok ? null : res.status };
};

export const actions: Actions = {
	/** One payment on a member's own fee (outside a registration): `payload` is the JSON of a `PaymentIn`, `membership_id` says whose. */
	payMember: async ({ request, fetch }) => {
		const f = await request.formData();
		let body: PaymentIn;
		try {
			body = JSON.parse(String(f.get('payload') ?? ''));
		} catch {
			return fail(400, { op: 'payMember', status: 400, fields: {} as Record<string, string>, message: '' });
		}
		const { allocations: _ignored, ...rest } = body;
		const res = await api('POST', `/finance/memberships/${String(f.get('membership_id') ?? '')}/payments`, { body: rest, f: fetch });
		return res.ok ? { done: 'payMember' } : fail(res.status >= 400 && res.status < 600 ? res.status : 500, { op: 'payMember', status: res.status, fields: res.fields, message: res.fields.__all__ || res.message || '' });
	}
};
