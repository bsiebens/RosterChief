import { error, fail } from '@sveltejs/kit';
import type { MemberAttendance } from '#lib/attendance.ts';
import { api } from '#lib/management-api.ts';
import { grantLoginFor } from '#lib/members/form-server.ts';
import { addPersonToFamily, familyCall } from '#lib/families/server.ts';
import type { FamilyDetail, GuardianContact, MemberDetail, Season, ShellMe } from '#lib/members/detail-types.ts';
import type { Officiating, OfficiatingOptions } from '#lib/members/eligibility.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch, parent }) => {
	const { me } = (await parent()) as { me?: ShellMe & { features?: string[] } };
	const res = await api<MemberDetail>('GET', `/members/${params.id}`, { f: fetch });
	if (!res.ok) error(res.status === 404 ? 404 : res.status || 500);
	const member = res.data!;

	// Season names (the member payload only has ids), the people in each family and this season's attendance, in parallel.
	// Attendance feeds both the hero tile (every tab) and the Attendance tab; the load does not read `?tab`, so switching
	// tabs reuses it instead of fetching again. A failure only blanks those boxes.
	const [seasons, attendanceRes, familyRes] = await Promise.all([
		api<Season[]>('GET', '/memberships/seasons', { f: fetch }),
		api<MemberAttendance>('GET', `/members/${params.id}/attendance`, { f: fetch }),
		Promise.all(member.families.map((f) => api<FamilyDetail>('GET', `/members/families/${f.id}`, { f: fetch })))
	]);
	const families = familyRes.flatMap((r) => (r.ok && r.data ? [r.data] : []));

	// For a child: the guardians' phones, when the viewer may see those guardians (otherwise the lookup 404s and is skipped).
	const guardianIds = new Set<string>();
	for (const link of member.families) {
		if (link.role !== 'child') continue;
		for (const m of families.find((f) => f.id === link.id)?.members ?? []) if (['parent', 'guardian'].includes(m.role) && m.member_id !== member.id) guardianIds.add(m.member_id);
	}
	const guardians: GuardianContact[] = (
		await Promise.all([...guardianIds].map((id) => api<MemberDetail>('GET', `/members/${id}`, { f: fetch })))
	).flatMap((r) => (r.ok && r.data && (r.data.phone || r.data.emergency_phone) ? [{ id: r.data.id, name: `${r.data.first_name} ${r.data.last_name}`.trim(), phone: r.data.phone, emergency_phone: r.data.emergency_phone, phone_display: r.data.phone_display, emergency_phone_display: r.data.emergency_phone_display }] : []));

	// Referee / official eligibility: only for admins of a club that has the feature; otherwise no calls are made.
	const hasOfficiating = !!me?.features?.includes('referee_officials');
	let officiating: Officiating = { referee: null, official: null };
	let officiatingOptions: OfficiatingOptions = { referee_levels: [], official_levels: [], teams: [] };
	if (hasOfficiating) {
		const [o, opts] = await Promise.all([api<Officiating>('GET', `/members/${params.id}/officiating`, { f: fetch }), api<OfficiatingOptions>('GET', '/members/officiating/options', { f: fetch })]);
		if (o.ok && o.data) officiating = o.data;
		if (opts.ok && opts.data) officiatingOptions = opts.data;
	}

	const attendance = attendanceRes.ok ? (attendanceRes.data ?? null) : null;
	return { member, seasons: seasons.data ?? [], attendance, families, guardians, isAdmin: !!me?.can_manage_members, hasOfficiating, officiating, officiatingOptions };
};

export const actions: Actions = {
	saveEligibility: async ({ params, request, fetch }) => {
		const form = await request.formData();
		const kind = form.get('kind') === 'official' ? 'official' : 'referee';
		const s = (k: string) => String(form.get(k) ?? '').trim();
		const body: Record<string, unknown> = { level_id: s('level_id') || null, valid_until: s('valid_until') || null };
		if (kind === 'referee') body.excluded_team_ids = form.getAll('excluded_team_ids').map(String);
		const res = await api('PUT', `/members/${params.id}/${kind}`, { body, f: fetch });
		if (!res.ok) return fail(res.status || 500, { eligibilityError: res.message || 'failed', eligibilityFields: res.fields, eligibilityKind: kind });
		return { eligibilityDone: kind };
	},
	removeEligibility: async ({ params, request, fetch }) => {
		const form = await request.formData();
		const kind = form.get('kind') === 'official' ? 'official' : 'referee';
		const res = await api('DELETE', `/members/${params.id}/${kind}`, { f: fetch });
		if (!res.ok) return fail(res.status || 500, { eligibilityError: res.message || 'failed', eligibilityFields: res.fields, eligibilityKind: kind });
		return { eligibilityDone: kind };
	},
	grantLogin: async ({ params, request, fetch }) => {
		const form = await request.formData();
		const r = await grantLoginFor(params.id, String(form.get('email') ?? '').trim(), fetch);
		if (!r.ok) return fail(r.status, { grantError: r.message || Object.values(r.fields)[0] || 'failed', grantFields: r.fields });
		return { granted: { email: r.email, created: r.created, emailSent: r.emailSent } };
	},
	addMembership: async ({ params, request, fetch }) => {
		const form = await request.formData();
		const s = (k: string) => String(form.get(k) ?? '').trim();
		const body = {
			member_id: params.id,
			season_id: s('season_id'),
			status: s('status') || 'pending',
			fee_amount: s('fee_amount') || '0',
			early_payment_deadline: s('early_payment_deadline') || null,
			early_payment_discount: s('early_payment_discount') || '0',
			signed_up_at: s('signed_up_at') || null
		};
		const res = await api('POST', '/memberships', { body, f: fetch });
		if (!res.ok) return fail(res.status || 500, { membershipError: res.message || 'failed', membershipFields: res.fields });
		return { membershipAdded: true };
	},
	deleteMembership: async ({ request, fetch }) => {
		const form = await request.formData();
		const res = await api('DELETE', `/memberships/${String(form.get('membership_id') ?? '')}`, { f: fetch });
		if (!res.ok) return fail(res.status || 500, { membershipActionError: res.fields.__all__ || res.message || 'failed', membershipActionFields: res.fields, membershipAction: 'deleteMembership' });
		return { membershipActionDone: 'deleteMembership' };
	},
	activate: ({ request, fetch }) => membershipAction(request, fetch, 'activate', (id) => `/memberships/${id}/activate`),
	waive: ({ request, fetch }) => membershipAction(request, fetch, 'waive', (id) => `/memberships/${id}/waive`, (f) => ({ waived: f.get('waived') === 'true' })),
	markPaid: ({ request, fetch }) => membershipAction(request, fetch, 'markPaid', (id) => `/memberships/${id}/mark-paid`, () => ({})),
	recordPayment: ({ request, fetch }) =>
		membershipAction(request, fetch, 'recordPayment', (id) => `/memberships/${id}/payments`, (f) => {
			const s = (k: string) => String(f.get(k) ?? '').trim();
			return { amount: s('amount'), method: s('method') || 'bank_transfer', reference: s('reference'), note: s('note'), paid_at: s('paid_at') ? new Date(s('paid_at')).toISOString() : null };
		}),
	addToFamily: async ({ params, request, fetch }) => {
		const form = await request.formData();
		const familyId = String(form.get('family_id') ?? '');
		const { failure } = await familyCall('addToFamily', 'POST', `/members/families/${familyId}/members`, fetch, { member_id: params.id, role: String(form.get('role') ?? 'parent') });
		return failure ?? { familyDone: 'addToFamily' };
	},
	newFamily: async ({ params, request, fetch }) => {
		const form = await request.formData();
		const { failure } = await familyCall('newFamily', 'POST', '/members/families', fetch, { name: String(form.get('name') ?? '').trim(), members: [{ member_id: params.id, role: String(form.get('role') ?? 'parent') }] });
		return failure ?? { familyDone: 'newFamily' };
	},
	addFamilyPerson: async ({ request, fetch }) => {
		const form = await request.formData();
		return (await addPersonToFamily('addFamilyPerson', String(form.get('family_id') ?? ''), form, fetch)) ?? { familyDone: 'addFamilyPerson' };
	},
	leaveFamily: async ({ params, request, fetch }) => {
		const form = await request.formData();
		const { failure } = await familyCall('leaveFamily', 'DELETE', `/members/families/${String(form.get('family_id') ?? '')}/members/${String(form.get('member_id') ?? '') || params.id}`, fetch);
		return failure ?? { familyDone: 'leaveFamily' };
	}
};

/** POSTs one of the fee/activation endpoints for the membership id in the form; errors come back as `fail()` data for the card. */
async function membershipAction(request: Request, f: typeof fetch, action: string, path: (id: string) => string, body?: (form: FormData) => unknown) {
	const form = await request.formData();
	const id = String(form.get('membership_id') ?? '');
	const res = await api('POST', path(id), { body: body ? body(form) : undefined, f });
	if (!res.ok) return fail(res.status || 500, { membershipActionError: res.message || 'failed', membershipActionFields: res.fields, membershipAction: action });
	return { membershipActionDone: action };
}
