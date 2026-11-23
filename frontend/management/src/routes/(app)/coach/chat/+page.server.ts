import { error } from '@sveltejs/kit';
import { chatEnabled } from '#lib/chat.ts';
import type { PageServerLoad } from './$types';

/** The chat screen needs the `chat` feature and permission. */
export const load: PageServerLoad = async ({ parent }) => {
	const { me } = await parent();
	if (!chatEnabled(me)) error(404, 'Not found');
	return {};
};
