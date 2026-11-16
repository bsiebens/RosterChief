import { error } from '@sveltejs/kit';
import { api } from '#lib/management-api.ts';
import type { PageServerLoad } from './$types';

export type Person = {
	member_id: string;
	first_name: string;
	last_name: string;
	photo_url: string | null;
	kind: 'referee' | 'official';
	level_id: string | null;
	level_name: string | null;
	valid_until: string | null;
	is_valid: boolean;
	eligible_team_names: string[];
	inherited_team_names?: string[];
	excluded_team_names: string[];
};
type Level = { id: string; name: string };

export const load: PageServerLoad = async ({ url, fetch, parent }) => {
	const { me } = (await parent()) as { me: { features: string[] } };
	if (!me.features.includes('referee_officials')) error(404);
	const sp = url.searchParams;
	const kind = ['referee', 'official'].includes(sp.get('kind') ?? '') ? (sp.get('kind') as string) : 'all';
	const valid = ['valid', 'expired'].includes(sp.get('valid') ?? '') ? (sp.get('valid') as string) : 'all';
	const filters = { kind, q: (sp.get('q') ?? '').trim(), level: sp.get('level') ?? '', valid };
	const [people, refs, offs] = await Promise.all([
		api<Person[]>('GET', '/officiating/people', { query: { ...filters, level: filters.level || undefined, q: filters.q || undefined }, f: fetch }),
		api<Level[]>('GET', '/officiating/referee-levels', { f: fetch }),
		api<Level[]>('GET', '/officiating/official-levels', { f: fetch })
	]);
	return {
		filters,
		people: people.data ?? [],
		refereeLevels: refs.data ?? [],
		officialLevels: offs.data ?? [],
		error: people.ok ? null : people.status
	};
};
