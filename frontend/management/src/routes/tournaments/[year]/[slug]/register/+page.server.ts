import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** The registration page exists only while the tournament is a draft or published (the API answers 404 to a POST otherwise); the form itself shows the closed / full state. */
export const load: PageServerLoad = async ({ parent, setHeaders }) => {
	const { tournament } = await parent();
	if (tournament.status !== 'draft' && tournament.status !== 'published') error(404);
	setHeaders({ 'x-robots-tag': 'noindex, nofollow' });
	return {};
};
