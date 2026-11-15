import { error, fail, redirect } from '@sveltejs/kit';
import { api, type ApiCall } from '#lib/management-api.ts';
import type { ChecklistDetail, Meeting, Review } from '#lib/evaluations.ts';
import type { Ref } from '#lib/events/types.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch, parent }) => {
	const { me } = await parent();
	if (!me.can_execute_evaluations) redirect(303, '/evaluations');
	const since = /^\d{4}-\d{2}-\d{2}$/.test(url.searchParams.get('since') ?? '') ? url.searchParams.get('since')! : '';
	const [checklist, players, meeting, release] = await Promise.all([
		api<ChecklistDetail>('GET', `/evaluations/checklists/${params.id}`, { f: fetch }),
		api<{ count: number; results: Ref[] }>('GET', `/evaluations/checklists/${params.id}/players`, { query: { since }, f: fetch }),
		api<Meeting>('GET', `/evaluations/checklists/${params.id}/meeting`, { f: fetch }),
		api<{ count: number }>('GET', `/evaluations/checklists/${params.id}/release`, { f: fetch })
	]);
	if (!checklist.ok) error(checklist.status >= 400 && checklist.status < 600 ? checklist.status : 500, checklist.message);
	const list = players.data?.results ?? [];
	const asked = url.searchParams.get('player');
	const selected = list.find((p) => p.id === asked)?.id ?? list[0]?.id ?? '';
	const review = selected ? await api<Review>('GET', `/evaluations/checklists/${params.id}/players/${selected}`, { f: fetch }) : null;
	return { checklist: checklist.data!, players: list, since, selected, review: review?.ok ? review.data! : null, meeting: meeting.data ?? { active: null, latest: null, outcomes: [] }, unreleased: release.ok ? (release.data?.count ?? 0) : 0 };
};

const httpStatus = (s: number) => (s >= 400 && s < 600 ? s : 500);
const failed = (op: string, res: ApiCall<unknown>) => fail(httpStatus(res.status), { op, fields: res.fields, message: res.fields.__all__ || res.message || '' });

export const actions: Actions = {
	start: async ({ params, fetch }) => {
		const res = await api('POST', `/evaluations/checklists/${params.id}/meeting`, { f: fetch });
		return res.ok ? { done: 'start' } : failed('start', res);
	},
	end: async ({ request, fetch }) => {
		const res = await api('POST', `/evaluations/sessions/${String((await request.formData()).get('session_id') ?? '')}/end`, { f: fetch });
		return res.ok ? { done: 'end' } : failed('end', res);
	},
	release: async ({ request, params, fetch }) => {
		const level = String((await request.formData()).get('release') ?? '');
		const res = await api<{ count: number }>('POST', `/evaluations/checklists/${params.id}/release`, { body: { release: level }, f: fetch });
		return res.ok ? { done: 'release', shared: res.data?.count ?? 0 } : failed('release', res);
	},
	note: async ({ request, params, fetch }) => {
		const f = await request.formData();
		const res = await api('POST', `/evaluations/checklists/${params.id}/players/${String(f.get('player_id') ?? '')}/notes`, { body: { note: String(f.get('note') ?? '') }, f: fetch });
		return res.ok ? { done: 'note' } : failed('note', res);
	},
	deleteNote: async ({ request, fetch }) => {
		const res = await api('DELETE', `/evaluations/notes/${String((await request.formData()).get('note_id') ?? '')}`, { f: fetch });
		return res.ok ? { done: 'deleteNote' } : failed('deleteNote', res);
	},
	outcome: async ({ request, fetch }) => {
		const f = await request.formData();
		const res = await api('PUT', `/evaluations/sessions/${String(f.get('session_id') ?? '')}/outcomes/${String(f.get('player_id') ?? '')}`, { body: { decision: String(f.get('decision') ?? '') }, f: fetch });
		return res.ok ? { done: 'outcome' } : failed('outcome', res);
	}
};
