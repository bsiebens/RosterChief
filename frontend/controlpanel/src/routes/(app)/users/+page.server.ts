import { error } from '@sveltejs/kit';
import { platform } from '#lib/api.ts';
import type { Page } from '#lib/clubs.ts';
import type { UserRow } from '#lib/users.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;
const pick = (v: string | null, allowed: string[], fallback: string) => (v && allowed.includes(v) ? v : fallback);

export const load: PageServerLoad = async ({ fetch, url, locals }) => {
	const a = locals.auth;
	if (a.status !== 'authenticated' || a.me.level !== 'superuser') error(403, 'Only superusers can look up user accounts.');

	const sp = url.searchParams;
	const query = {
		q: sp.get('q')?.trim() ?? '',
		status: pick(sp.get('status'), ['active', 'blocked', 'all'], 'all'),
		mfa: pick(sp.get('mfa'), ['yes', 'no', 'any'], 'any'),
		level: pick(sp.get('level'), ['superuser', 'staff', 'none', 'any'], 'any'),
		page: Math.max(1, Number(sp.get('page')) || 1)
	};

	const params = new URLSearchParams({ status: query.status, mfa: query.mfa, level: query.level, limit: String(PAGE_SIZE), offset: String((query.page - 1) * PAGE_SIZE) });
	if (query.q) params.set('q', query.q);

	const users = await platform<Page<UserRow>>('GET', `/users?${params}`, undefined, fetch);
	return {
		users: users.data?.items ?? [],
		total: users.data?.count ?? 0,
		error: users.ok ? '' : (users.message ?? 'The users could not be loaded.'),
		query,
		pageSize: PAGE_SIZE
	};
};
