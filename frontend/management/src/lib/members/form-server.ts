import { error, fail, redirect, type RequestEvent } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { formToBody, valuesFromForm } from './detail-helpers.ts';
import type { MemberDetail, ShellMe } from './detail-types.ts';

/** Only holders of members.manage_members reach the create/edit pages. `redirectTo` sends everyone else to the detail page instead of a 403. */
export async function requireAdmin(parent: () => Promise<unknown>, redirectTo?: string): Promise<void> {
	const { me } = (await parent()) as { me?: ShellMe };
	if (me?.can_manage_members) return;
	if (redirectTo) redirect(303, redirectTo);
	error(403);
}

/** The groups pages: members managers, and the admins of at least one group (who only see their own groups). */
export async function requireGroupAccess(parent: () => Promise<unknown>, redirectTo = '/'): Promise<void> {
	const { me } = (await parent()) as { me?: ShellMe };
	if (me?.can_manage_members || me?.admin_group_ids?.length) return;
	redirect(303, redirectTo);
}

/** The settings pages need admin.manage_settings (or the Owner role). */
export async function requireSettings(parent: () => Promise<unknown>, redirectTo = '/'): Promise<void> {
	const { me } = (await parent()) as { me?: ShellMe };
	if (me?.can_manage_settings) return;
	redirect(303, redirectTo);
}

/** Shared create/update action: 422 field errors and the plan-limit 403 come back as `fail()` data for the form. */
export async function saveMember(event: RequestEvent, id?: string) {
	const form = await event.request.formData();
	// Adding a person can carry a season membership, a family / guardians and a login invite (one hidden JSON field, see OnboardingFields).
	const extra = id ? {} : onboardingParts(form);
	const res = id
		? await api<MemberDetail>('PATCH', `/members/${id}`, { body: formToBody(form), f: event.fetch })
		: await api<MemberDetail & { invite?: { sent: boolean } | null; membership_id?: string | null }>('POST', '/members', { body: { ...formToBody(form), ...extra }, f: event.fetch });
	if (!res.ok) {
		if (res.status === 404) error(404);
		return fail(res.status >= 400 && res.status < 600 ? res.status : 500, { fields: res.fields, message: res.fields && Object.keys(res.fields).length ? '' : (res.message ?? ''), code: res.code ?? '', values: valuesFromForm(form) });
	}
	const memberId = res.data!.id;
	// Pictures: profile (`photo` endpoint) and official player picture (`player-photo`); each can be uploaded or removed.
	for (const [field, path] of [['profile_photo', 'photo'], ['player_photo', 'player-photo']] as const) {
		const file = form.get(field);
		const upload = file instanceof File && file.size > 0;
		if (!upload && form.get(`remove_${field}`) !== 'on') continue;
		const pr = upload
			? await api('POST', `/members/${memberId}/${path}`, { form: (() => { const f = new FormData(); f.set('photo', file); return f; })(), f: event.fetch })
			: await api('DELETE', `/members/${memberId}/${path}`, { f: event.fetch });
		// The member itself is saved at this point; stay on the form so the picture error can be fixed.
		if (!pr.ok) return fail(pr.status >= 400 && pr.status < 600 ? pr.status : 500, { fields: { [field]: pr.fields.photo || pr.message || '' }, message: '', code: pr.code ?? '', values: valuesFromForm(form) });
	}
	// A newly added person lands on their page with a note about what was set up (see the banner on the member page).
	if (!id) {
		const made = res.data as { membership_id?: string | null; invite?: { sent: boolean } | null };
		const q = new URLSearchParams({ added: '1' });
		if (made.membership_id) q.set('enrolled', (extra as { membership?: { status?: string } }).membership?.status ?? 'active');
		if (made.invite) q.set('invite', made.invite.sent ? 'sent' : 'unsent');
		redirect(303, `/members/${memberId}?${q}`);
	}
	redirect(303, `/members/${memberId}`);
}

/** The optional membership / family / guardians / invite chosen on the add form (empty when none, or when the field is missing or broken). */
function onboardingParts(form: FormData): Record<string, unknown> {
	try {
		const p = JSON.parse(String(form.get('ob_payload') ?? '{}')) as { membership?: unknown; family?: unknown; guardians?: unknown[]; invite?: unknown };
		return {
			...(p.membership ? { membership: p.membership } : {}),
			...(p.family ? { family: p.family } : {}),
			...(Array.isArray(p.guardians) && p.guardians.length ? { guardians: p.guardians } : {}),
			...(p.invite ? { invite: p.invite } : {})
		};
	} catch {
		return {};
	}
}

/**
 * Grant a member a login. When `email` is given (the member had none) it is saved on the member first, then the
 * login is created. Returns `{ ok: true, email, created }` or `{ ok: false, status, message, fields }`.
 */
export async function grantLoginFor(memberId: string, email: string, f: typeof fetch) {
	if (email) {
		const saved = await api('PATCH', `/members/${memberId}`, { body: { email }, f });
		if (!saved.ok) return { ok: false as const, status: saved.status || 500, message: saved.message ?? '', fields: saved.fields };
	}
	const res = await api<{ email: string; user_created: boolean; email_sent?: boolean }>('POST', `/members/${memberId}/grant-login`, { body: {}, f });
	if (!res.ok) return { ok: false as const, status: res.status || 500, message: res.message ?? '', fields: res.fields };
	return { ok: true as const, email: res.data!.email, created: res.data!.user_created, emailSent: !!res.data!.email_sent };
}
