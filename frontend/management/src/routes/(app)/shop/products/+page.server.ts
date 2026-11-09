import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, failed, shopOn, type Category, type PagedList, type ProductDetail, type ProductItem } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const ACTIVE = ['all', 'active', 'inactive'] as const;
type Active = (typeof ACTIVE)[number];

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_products')) redirect(303, '/');
	const q = (url.searchParams.get('q') ?? '').trim();
	const category = url.searchParams.get('category') ?? '';
	const requested = url.searchParams.get('active') as Active;
	const active: Active = ACTIVE.includes(requested) ? requested : 'all';
	const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);
	const [res, cats] = await Promise.all([
		api<PagedList<ProductItem> | ProductItem[]>('GET', '/shop/products', {
			query: { q, category_id: category, active: active === 'all' ? undefined : active === 'active', limit: PAGE_SIZE, offset: offset || undefined },
			f: fetch
		}),
		api<PagedList<Category> | Category[]>('GET', '/shop/categories', { f: fetch })
	]);
	const list = asList(res.data);
	return { filters: { q, category, active, offset }, rows: list.results, count: list.count, pageSize: PAGE_SIZE, categories: asList(cats.data).results, error: res.ok ? null : res.status };
};

export const actions: Actions = {
	delete: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api('DELETE', `/shop/products/${id}`, { f: fetch });
		return res.ok ? { done: 'delete', id } : failed('delete', res, { id });
	},
	/** The fallback when a product with orders (or stock history) cannot be deleted. */
	deactivate: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api<ProductDetail>('PATCH', `/shop/products/${id}`, { body: { is_active: false }, f: fetch });
		return res.ok ? { done: 'deactivate', id } : failed('deactivate', res, { id });
	},
	/** Create (no id) or rename (id) a category. */
	categorySave: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = String(form.get('id') ?? '');
		const name = String(form.get('name') ?? '').trim();
		const res = await api<Category>(id ? 'PATCH' : 'POST', id ? `/shop/categories/${id}` : '/shop/categories', { body: { name }, f: fetch });
		return res.ok ? { done: 'category_saved' } : failed('categorySave', res, { id });
	},
	categoryDelete: async ({ request, fetch }) => {
		const id = String((await request.formData()).get('id') ?? '');
		const res = await api('DELETE', `/shop/categories/${id}`, { f: fetch });
		return res.ok ? { done: 'category_deleted' } : failed('categoryDelete', res, { id });
	}
};
