import { fail, redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { requireSettings } from '#lib/members/form-server.ts';
import { clearHostInfo } from '#lib/server/backend.ts';
import type { Actions, PageServerLoad } from './$types';

export type ClubSettings = {
	name: string;
	legal_name: string;
	contact_email: string;
	website: string;
	legal_address: string;
	legal_zip_code: string;
	legal_city: string;
	legal_country: string;
	vat_id: string;
	payment_instructions: string;
	logo_url: string | null;
	event_background_url: string | null;
	primary_color: string;
	secondary_color: string;
	primary_text_color: string;
	secondary_text_color: string;
};
export type Country = { code: string; name: string };
export type PreviewItem = { key: string; kind: 'email' | 'pdf'; title: string; description: string };

const DETAIL_FIELDS = ['name', 'legal_name', 'contact_email', 'website', 'legal_address', 'legal_zip_code', 'legal_city', 'legal_country', 'vat_id', 'payment_instructions'];
const COLOR_FIELDS = ['primary_color', 'secondary_color', 'primary_text_color', 'secondary_text_color'];
const IMAGES = { logo: 'logo', event_background: 'event-background' };

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	// Someone who only manages roles lands on the roles page.
	const { me } = (await parent()) as { me?: { can_manage_settings?: boolean; can_manage_roles?: boolean; can_manage_members?: boolean } };
	if (!me?.can_manage_settings && me?.can_manage_roles) redirect(303, '/settings/roles');
	if (!me?.can_manage_settings && me?.can_manage_members) redirect(303, '/settings/requirements');
	await requireSettings(parent);
	const raw = url.searchParams.get('tab');
	const tab = raw === 'email' || raw === 'previews' ? 'email' : raw === 'pdf' ? 'pdf' : 'identity';
	if (tab !== 'identity') {
		const res = await api<PreviewItem[]>('GET', '/club/settings/previews', { f: fetch });
		return { tab, settings: null as ClubSettings | null, countries: [] as Country[], previews: res.data ?? [], error: res.ok ? null : res.status };
	}
	const [res, countries] = await Promise.all([api<ClubSettings>('GET', '/club/settings', { f: fetch }), api<Country[]>('GET', '/club/settings/countries', { f: fetch })]);
	return { tab, settings: res.data ?? null, countries: countries.data ?? [], previews: [] as PreviewItem[], error: res.ok ? null : res.status };
};

const pick = (form: FormData, names: string[]) => Object.fromEntries(names.map((n) => [n, String(form.get(n) ?? '').trim()]));
const failed = (action: string, res: { status: number; message?: string; fields: Record<string, string> }) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { settingsAction: action, settingsError: res.message || 'failed', settingsFields: res.fields });

export const actions: Actions = {
	save: async (event) => {
		const form = await event.request.formData();
		const res = await api('PATCH', '/club/settings', { body: pick(form, [...DETAIL_FIELDS, ...COLOR_FIELDS]), f: event.fetch });
		if (!res.ok) return failed('save', res);
		for (const [field, endpoint] of Object.entries(IMAGES)) {
			const path = `/club/settings/${endpoint}`;
			const file = form.get(field);
			let pr;
			if (file instanceof File && file.size > 0) {
				const upload = new FormData();
				upload.set('file', file);
				pr = await api('POST', path, { form: upload, f: event.fetch });
			} else if (form.get(`remove_${field}`) === 'on') {
				pr = await api('DELETE', path, { f: event.fetch });
			}
			if (pr && !pr.ok) {
				clearHostInfo(event);
				return failed('save', { ...pr, fields: { [field]: pr.fields.file || pr.message || '' } });
			}
		}
		clearHostInfo(event);
		return { settingsAction: 'save', saved: true };
	}
};
