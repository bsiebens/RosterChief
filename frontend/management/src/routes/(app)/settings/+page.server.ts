import { fail } from '@sveltejs/kit';
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
	primary_color: string;
	secondary_color: string;
	primary_text_color: string;
	secondary_text_color: string;
};
export type Country = { code: string; name: string };
export type PreviewItem = { key: string; kind: 'email' | 'pdf'; title: string; description: string };

const DETAIL_FIELDS = ['name', 'legal_name', 'contact_email', 'website', 'legal_address', 'legal_zip_code', 'legal_city', 'legal_country', 'vat_id', 'payment_instructions'];
const COLOR_FIELDS = ['primary_color', 'secondary_color', 'primary_text_color', 'secondary_text_color'];

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
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
		const file = form.get('logo');
		if (file instanceof File && file.size > 0) {
			const up = new FormData();
			up.set('file', file);
			const pr = await api('POST', '/club/settings/logo', { form: up, f: event.fetch });
			if (!pr.ok) {
				clearHostInfo(event);
				return failed('save', { ...pr, fields: { logo: pr.fields.file || pr.message || '' } });
			}
		} else if (form.get('remove_logo') === 'on') {
			const pr = await api('DELETE', '/club/settings/logo', { f: event.fetch });
			if (!pr.ok) return failed('save', { ...pr, fields: { logo: pr.message || '' } });
		}
		clearHostInfo(event);
		return { settingsAction: 'save', saved: true };
	}
};
