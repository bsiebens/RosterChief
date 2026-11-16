import { error } from '@sveltejs/kit';
import { platform, platformList } from '#lib/api.ts';
import type { AuditEntry, PlatformAdmin } from '#lib/admins.ts';
import type { ClubRow, Page } from '#lib/clubs.ts';
import type { PageServerLoad } from './$types';

const PAGE_SIZE = 25;

export const load: PageServerLoad = async ({ fetch, url, locals, depends }) => {
	depends('app:admins');
	const a = locals.auth;
	if (a.status !== 'authenticated' || a.me.level !== 'superuser') error(403, 'Only superusers can manage platform admins.');

	const q = url.searchParams;
	const query = { action: q.get('action') ?? '', actor: q.get('actor') ?? '', club: q.get('club') ?? '', target: q.get('target') ?? '', page: Math.max(1, Number(q.get('page')) || 1) };

	const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((query.page - 1) * PAGE_SIZE) });
	if (query.action) params.set('action', query.action);
	if (query.actor) params.set('actor_id', query.actor);
	if (query.club) params.set('club_id', query.club);
	if (query.target) params.set('target_type', query.target);

	const [admins, audit, clubs] = await Promise.all([
		platformList<PlatformAdmin>('/admins', fetch),
		platform<Page<AuditEntry>>('GET', `/audit-log?${params}`, undefined, fetch),
		platformList<ClubRow>('/clubs?status=all', fetch)
	]);

	return {
		me: { id: String(a.me.id), email: a.me.email },
		admins: admins.data ?? [],
		adminsError: admins.ok ? '' : (admins.message ?? 'The platform admins could not be loaded.'),
		entries: audit.data?.items ?? [],
		total: audit.data?.count ?? 0,
		auditError: audit.ok ? '' : (audit.message ?? 'The audit log could not be loaded.'),
		clubs: (clubs.data ?? []).map((c) => ({ id: c.id, name: c.name })),
		query,
		pageSize: PAGE_SIZE
	};
};
