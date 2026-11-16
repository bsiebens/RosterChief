import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** /shop has no page of its own: go to the first part of the shop this user may manage. */
export const load: PageServerLoad = async ({ parent }) => {
	const { me } = await parent();
	if (!me.features.includes('shop')) redirect(303, '/');
	if (me.can_manage_shop_products) redirect(303, '/shop/products');
	if (me.can_manage_shop_inventory) redirect(303, '/shop/inventory');
	if (me.can_manage_shop_orders) redirect(303, '/shop/orders');
	if (me.can_manage_shop_discounts) redirect(303, '/shop/discounts');
	if (me.can_manage_shop_invoices) redirect(303, '/shop/invoices');
	redirect(303, '/');
};
