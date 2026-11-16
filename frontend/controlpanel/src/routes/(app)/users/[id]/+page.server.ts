import { error } from '@sveltejs/kit';
import { platform } from '#lib/api.ts';
import type { UserPush } from '#lib/push.ts';
import type { UserDetail } from '#lib/users.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, depends, locals }) => {
	depends('app:user');
	const a = locals.auth;
	if (a.status !== 'authenticated' || a.me.level !== 'superuser') error(403, 'Only superusers can look up user accounts.');

	const r = await platform<UserDetail>('GET', `/users/${encodeURIComponent(params.id)}`, undefined, fetch);
	if (r.status === 404 || r.status === 422) error(404, 'User not found');
	if (!r.ok) error(500, r.message ?? 'The user could not be loaded');
	// The push section is optional: when it fails only that section shows an error.
	const push = await platform<UserPush>('GET', `/users/${encodeURIComponent(params.id)}/push`, undefined, fetch);
	return { user: r.data!, push: push.data ?? null, pushError: push.ok ? '' : (push.message ?? 'The push details could not be loaded.'), meId: String(a.me.id) };
};
