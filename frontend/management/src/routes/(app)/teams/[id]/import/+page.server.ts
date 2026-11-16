import { error, fail, redirect } from '@sveltejs/kit';
import type { Country, Location, Opponent } from '#lib/calendar.ts';
import type { FixtureImportResult, FixturePreview, FixtureSources, PlayDayPreview, PlayDayResult } from '#lib/fixtures.ts';
import { api, type ApiCall } from '#lib/management-api.ts';
import { loadSeasons, type TeamDetail } from '#lib/teams/server.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * The fixture import wizard of a team. Every step is a form action that calls the API server-side; the preview
 * `token` only lives in the page (the API keeps the fetched payload for 15 minutes behind it).
 */
export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = (await parent()) as { me?: { can_manage_settings?: boolean } };
	const [team, seasons, sources, opponents, locations, countries] = await Promise.all([
		api<TeamDetail>('GET', `/teams/${params.id}`, { f: fetch }),
		loadSeasons(fetch),
		api<FixtureSources>('GET', `/teams/${params.id}/fixture-sources`, { f: fetch }),
		api<Opponent[]>('GET', '/opponents', { f: fetch }),
		api<Location[]>('GET', '/locations', { f: fetch }),
		api<Country[]>('GET', '/locations/countries', { f: fetch })
	]);
	if (!team.ok) error(team.status === 404 ? 404 : team.status || 500);
	const canImport = sources.ok ? !!sources.data?.can_import : !!me?.can_manage_settings;
	if (!canImport) redirect(303, `/teams/${params.id}`);
	const q = (k: string) => url.searchParams.get(k) ?? '';
	return {
		team: { id: team.data!.id, name: team.data!.name },
		seasons,
		available: sources.data?.available ?? [],
		opponents: (opponents.data ?? []).map((o) => ({ id: o.id, name: o.name })),
		locations: (locations.data ?? []).map((l) => ({ id: l.id, name: l.name, city: l.city, is_home: l.is_home })),
		countries: countries.data ?? [],
		// "Review changes" on the team page (and the import button) prefill step 1; `run` starts the preview at once.
		prefill: { source: q('source'), url: q('url'), season: q('season'), label: q('label'), past: q('past') === '1', run: q('run') === '1' }
	};
};

type Input = { source: string; url: string; season_id: string; competition_label: string; include_past: boolean };
const text = (form: FormData, k: string) => String(form.get(k) ?? '').trim();
const failure = (step: string, res: ApiCall<unknown>, extra: Record<string, unknown> = {}) =>
	fail(res.status >= 400 && res.status < 600 ? res.status : 500, { step, status: res.status, code: res.code ?? '', error: res.message || (res.status === 0 ? 'network' : 'failed'), fields: res.fields, ...extra });
const parse = (raw: string) => {
	try {
		return JSON.parse(raw || '{}') as Record<string, unknown>;
	} catch {
		return null;
	}
};

export const actions: Actions = {
	preview: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const input: Input = { source: text(form, 'source'), url: text(form, 'url'), season_id: text(form, 'season_id'), competition_label: text(form, 'competition_label'), include_past: text(form, 'include_past') === '1' };
		const res = await api<FixturePreview>('POST', `/teams/${params.id}/fixture-sources/preview`, { body: { ...input, season_id: input.season_id || null }, f: fetch });
		if (!res.ok) return failure('source', res, { input });
		return { step: 'review', preview: res.data!, input };
	},
	import: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const choices = parse(text(form, 'choices'));
		if (!choices) return fail(400, { step: 'review', status: 400, code: '', error: 'Invalid data.', fields: {} });
		const res = await api<FixtureImportResult>('POST', `/teams/${params.id}/fixture-sources/import`, { body: { token: text(form, 'token'), ...choices }, f: fetch });
		if (!res.ok) return failure('review', res);
		return { step: 'done', result: res.data! };
	},
	jsonPreview: async ({ request, fetch }) => {
		const form = await request.formData();
		const file = form.get('file');
		if (!(file instanceof File) || file.size === 0) return fail(422, { step: 'source', status: 422, code: 'invalid', error: '', fields: { file: 'required' } });
		const up = new FormData();
		up.set('file', file);
		const res = await api<PlayDayPreview>('POST', '/fixture-imports/json/preview', { form: up, f: fetch });
		if (!res.ok) return failure('source', res);
		return { step: 'jsonReview', jsonPreview: res.data!, filename: file.name };
	},
	jsonImport: async ({ request, fetch }) => {
		const form = await request.formData();
		const locations = parse(text(form, 'locations'));
		if (!locations) return fail(400, { step: 'jsonReview', status: 400, code: '', error: 'Invalid data.', fields: {} });
		const body = { token: text(form, 'token'), locations, acknowledge_warnings: form.get('acknowledge_warnings') === 'on' };
		const res = await api<PlayDayResult>('POST', '/fixture-imports/json/import', { body, f: fetch });
		if (!res.ok) return failure('jsonReview', res);
		return { step: 'jsonDone', jsonResult: res.data! };
	}
};
