import { error } from '@sveltejs/kit';
import { chatEnabled } from '#lib/chat.ts';
import type { PageServerLoad } from './$types';

/** The chat screen needs the `chat` feature and permission; a parent-only account has no staff `me`, so it is let through and the API decides. */
export const load: PageServerLoad = async ({ parent }) => {
	const { me } = await parent();
	if (me && !chatEnabled(me)) error(404, 'Not found');
	return {};
};
