import { redirect } from '@sveltejs/kit';
import type { FixtureSources } from '#lib/fixtures.ts';
import { requireSettings } from '#lib/members/form-server.ts';
import { api } from '#lib/management-api.ts';
import type { TeamRow } from '#lib/teams/server.ts';
import type { PageServerLoad } from './$types';

/** Pick the team to import fixtures for: the wizard itself is per team (`/teams/{id}/import`). Settings holders only. */
export const load: PageServerLoad = async ({ parent, fetch }) => {
	await requireSettings(parent, '/calendar');
	const teams = await api<TeamRow[]>('GET', '/teams', { f: fetch });
	if (!teams.ok) redirect(303, '/calendar');
	// What each team already has set up (a failure just shows the card without it).
	const rows = await Promise.all(
		(teams.data ?? []).map(async (team) => {
			const res = await api<FixtureSources>('GET', `/teams/${team.id}/fixture-sources`, { f: fetch });
			return { team, sources: res.ok ? (res.data?.sources ?? []) : null, anyEnabled: res.ok ? (res.data?.available ?? []).some((a) => a.enabled) : true };
		})
	);
	return { rows };
};
