import { fail } from '@sveltejs/kit';
import type { Country, Location } from '#lib/calendar.ts';
import { api } from '#lib/management-api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch }) => {
	const q = (url.searchParams.get('q') ?? '').trim();
	const [locations, countries] = await Promise.all([api<Location[]>('GET', '/locations', { query: { q }, f: fetch }), api<Country[]>('GET', '/locations/countries', { f: fetch })]);
	return { q, locations: locations.data ?? [], countries: countries.data ?? [], error: locations.ok ? null : locations.status };
};

const failed = (action: string, res: { status: number; message?: string; fields: Record<string, string> }) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { locAction: action, locError: res.fields.__all__ || res.message || 'failed', locFields: res.fields });

const num = (form: FormData, k: string) => {
	const v = String(form.get(k) ?? '').trim().replace(',', '.');
	return v === '' || Number.isNaN(Number(v)) ? null : Number(v);
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const s = (k: string) => String(form.get(k) ?? '').trim();
		const body = { name: s('name'), address: s('address'), zip_code: s('zip_code'), city: s('city'), country: s('country'), latitude: num(form, 'latitude'), longitude: num(form, 'longitude') };
		const res = id ? await api('PATCH', `/locations/${id}`, { body, f: fetch }) : await api('POST', '/locations', { body, f: fetch });
		return res.ok ? { done: 'save' } : failed('save', res);
	},
	delete: async ({ request, fetch }) => {
		const form = await request.formData();
		const res = await api('DELETE', `/locations/${String(form.get('id') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'delete' } : failed('delete', res);
	}
};
