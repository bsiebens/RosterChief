import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, badPayload, failed, parsePayload, shopOrdersOn, type BatchDetail, type BatchItem, type PagedList, type PendingLine } from '#lib/shop-orders.ts';
import type { Actions, PageServerLoad } from './$types';

const TABS = ['to_order', 'ordered', 'received'] as const;
type Tab = (typeof TABS)[number];

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOrdersOn(me)) redirect(303, '/');
	const requested = url.searchParams.get('tab') as Tab;
	const tab: Tab = TABS.includes(requested) ? requested : 'to_order';
	const [lines, batches] = await Promise.all([
		api<PagedList<PendingLine> | PendingLine[]>('GET', '/shop/production/lines', { query: { limit: 200 }, f: fetch }),
		api<PagedList<BatchItem> | BatchItem[]>('GET', '/shop/production/batches', { query: { limit: 200 }, f: fetch })
	]);
	const pending = asList(lines.data);
	const all = asList(batches.data).results;
	// Batches still moving (not fully received) are read with their lines; received ones only as a list.
	const open = await Promise.all(
		all
			.filter((b) => b.status !== 'received')
			.map(async (b) => {
				const d = await api<BatchDetail>('GET', `/shop/production/batches/${b.id}`, { f: fetch });
				return d.ok ? d.data! : { ...b, lines: [] };
			})
	);
	return {
		tab,
		lines: pending.results,
		lineCount: pending.count,
		open,
		received: all.filter((b) => b.status === 'received'),
		error: lines.ok && batches.ok ? null : (lines.status || batches.status)
	};
};

const str = (f: FormData, k: string) => String(f.get(k) ?? '').trim();

export const actions: Actions = {
	createBatch: async ({ request, fetch }) => {
		const body = parsePayload<{ line_ids: string[]; reference: string; supplier: string; note: string }>(await request.formData());
		if (!body) return badPayload('createBatch');
		const res = await api<BatchDetail>('POST', '/shop/production/batches', { body, f: fetch });
		if (!res.ok) return failed('createBatch', res);
		redirect(303, '/shop/production?tab=ordered&created=1');
	},
	editBatch: async ({ request, fetch }) => {
		const f = await request.formData();
		const id = str(f, 'id');
		const res = await api<BatchDetail>('PATCH', `/shop/production/batches/${id}`, { body: { reference: str(f, 'reference'), supplier: str(f, 'supplier'), note: str(f, 'note') }, f: fetch });
		return res.ok ? { done: 'editBatch' } : failed('editBatch', res, { id });
	},
	deleteBatch: async ({ request, fetch }) => {
		const id = str(await request.formData(), 'id');
		const res = await api('DELETE', `/shop/production/batches/${id}`, { f: fetch });
		return res.ok ? { done: 'deleteBatch' } : failed('deleteBatch', res, { id });
	},
	markOrdered: async ({ request, fetch }) => {
		const id = str(await request.formData(), 'id');
		const res = await api<BatchDetail>('POST', `/shop/production/batches/${id}/mark-ordered`, { f: fetch });
		return res.ok ? { done: 'markOrdered' } : failed('markOrdered', res, { id });
	},
	receive: async ({ request, fetch }) => {
		const f = await request.formData();
		const id = str(f, 'id');
		const items = str(f, 'mode') === 'entered' ? parsePayload<{ line_id: string; quantity: number }[] | null>(f, 'items') : null;
		const res = await api<BatchDetail>('POST', `/shop/production/batches/${id}/receive`, { body: items && items.length ? { items } : {}, f: fetch });
		return res.ok ? { done: 'receive', id } : failed('receive', res, { id });
	}
};
