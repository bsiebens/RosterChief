import { redirect } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import { asList, badPayload, emptyProduct, failed, parsePayload, productHref, shopOn, type Category, type PagedList, type ProductDetail, type ProductIn, type VariantIn } from '#lib/shop.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ parent, fetch }) => {
	const { me } = await parent();
	if (!shopOn(me, 'can_manage_shop_products')) redirect(303, '/');
	const [cats, seasons, teams] = await Promise.all([api<PagedList<Category> | Category[]>('GET', '/shop/categories', { f: fetch }), api<{ id: string; name: string }[]>('GET', '/memberships/seasons', { f: fetch }), api<{ id: string; name: string }[]>('GET', '/teams', { f: fetch })]);
	return { initial: emptyProduct(), categories: asList(cats.data).results, seasons: seasons.data ?? [], teams: (teams.data ?? []).map((x) => ({ id: x.id, name: x.name })) };
};

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const body = parsePayload<ProductIn & { variants: VariantIn[] }>(await request.formData());
		if (!body) return badPayload('save');
		const variants = (body.variants ?? []).filter((v) => v.name.trim()).map((v) => ({ name: v.name.trim(), price: v.price || null, team_id: v.team_id || null }));
		const res = await api<ProductDetail>('POST', '/shop/products', { body: { ...body, variants }, f: fetch });
		if (!res.ok) return failed('save', res);
		redirect(303, `${productHref(res.data!.id)}?created=1`);
	}
};
