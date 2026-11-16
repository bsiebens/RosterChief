import { api } from '#lib/management-api.ts';
import type { LayoutServerLoad } from './$types';

/** Whether the shop is closed for members, for the banner on every shop page (the public status endpoint: any shop permission may read it). */
export const load: LayoutServerLoad = async ({ fetch, parent }) => {
	const { me } = await parent();
	if (!me?.features?.includes('shop')) return { shopStatus: null as { open: boolean; message: string } | null };
	const res = await api<{ open: boolean; message: string }>('GET', '/public/shop/status', { f: fetch });
	return { shopStatus: res.ok ? (res.data ?? null) : null };
};
