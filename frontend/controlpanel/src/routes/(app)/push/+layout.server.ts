import { error } from '@sveltejs/kit';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ locals }) => {
	const a = locals.auth;
	if (a.status !== 'authenticated' || a.me.level !== 'superuser') error(403, 'Only superusers can use the push tools.');
	return {};
};
