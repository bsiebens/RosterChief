import { error } from '@sveltejs/kit';
import { platform } from '#lib/api.ts';
import type { UserDetail } from '#lib/users.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, depends, locals }) => {
	depends('app:user');
	const a = locals.auth;
	if (a.status !== 'authenticated' || a.me.level !== 'superuser') error(403, 'Only superusers can look up user accounts.');

	const r = await platform<UserDetail>('GET', `/users/${encodeURIComponent(params.id)}`, undefined, fetch);
	if (r.status === 404 || r.status === 422) error(404, 'User not found');
	if (!r.ok) error(500, r.message ?? 'The user could not be loaded');
	return { user: r.data!, meId: String(a.me.id) };
};
