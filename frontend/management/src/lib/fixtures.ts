/**
 * Fixture imports (`/teams/{id}/fixture-sources/*`, `/fixture-imports/json/*`): the shapes the API returns
 * (api/schemas_fixture_imports.py) and the small helpers the team page and the import wizard share.
 */
import { t } from '#lib/i18n.svelte.ts';

export type SourceCode = 'RBIHF' | 'DFEL' | 'IJNL';
export type Ref = { id: string; name: string };
export type LocationRef = Ref & { city: string };
export type Change = { old: string | null; new: string | null };

export type LastCheck = { rescheduled: number; new_games: number; removed_games: number; error: string };
export type FixtureSource = {
	id: string;
	source: SourceCode;
	url: string;
	season_id: string | null;
	season_name: string;
	season_exists: boolean;
	competition_label: string;
	include_past?: boolean;
	last_imported: string | null;
	last_checked: string | null;
	last_check: LastCheck | null;
	enabled: boolean;
	has_standings: boolean;
};
export type AvailableSource = { source: SourceCode; name: string; feature: string; feature_name: string; help: string; example: string; enabled: boolean };
export type FixtureSources = { sources: FixtureSource[]; available: AvailableSource[]; can_import: boolean };

export type GameAction = 'create' | 'update' | 'remove' | 'unchanged';
export type PlannedGame = {
	action: GameAction;
	game_id: string;
	kickoff: string;
	is_home: boolean;
	our_side: 'home' | 'away';
	home: string;
	away: string;
	title: string;
	opponent_name: string;
	venue: string;
	opponent: Ref | null;
	location: Ref | null;
	event_id: string | null;
	score_for: number | null;
	score_against: number | null;
	changes: Record<string, Change>;
};
export type UnknownOpponent = { source_name: string; game_ids: string[]; current: Ref | null; default: 'keep' | 'create'; suggestions: Ref[] };
export type UnknownLocation = { source_text: string; game_ids: string[]; suggestions: LocationRef[] };
export type FixturePreview = {
	token: string;
	source: SourceCode;
	url: string;
	season_id: string | null;
	season_name: string;
	competition_label: string;
	include_past: boolean;
	scraped_team_name: string;
	team: Ref;
	counts: Record<GameAction, number>;
	has_changes: boolean;
	games: PlannedGame[];
	unknown_opponents: UnknownOpponent[];
	unknown_locations: UnknownLocation[];
	warnings: string[];
};
export type FixtureImportResult = { created: number; updated: number; deleted: number; opponents_created: number; locations_created: number; sources: FixtureSource[] };

export type Check = {
	rescheduled: { game_id: string; old_start: string; new_start: string }[];
	new_games: { game_id: string; start: string; opponent_name: string }[];
	removed_games: { game_id: string; start: string; title: string }[];
	error: string;
	has_changes: boolean;
};
export type Recheck = { source: FixtureSource; check: Check; preview: FixturePreview | null };
export type RecheckAll = { season_id: string | null; results: Recheck[]; skipped: { id: string; source: string; reason: string }[] };

export type PlayDay = {
	action: 'create' | 'update';
	game_id: string;
	start: string;
	end: string | null;
	city: string;
	teams: Ref[];
	hosted_by_us: boolean;
	label: string;
	location: Ref | null;
	warning: string | null;
	event_id: string | null;
	changes: Record<string, Change>;
};
export type PlayDayPreview = { token: string; source: string; competition: string; title: string; counts: { create: number; update: number; unchanged: number }; has_changes: boolean; games: PlayDay[] };
export type PlayDayResult = { created: number; updated: number };

/** "0 rescheduled, 1 new, 0 removed", or the error of the last check. */
export const checkSummary = (c: LastCheck | null | undefined) => (!c ? '' : c.error ? c.error : t('fixtures.summary', { rescheduled: c.rescheduled, new: c.new_games, removed: c.removed_games }));

/** The wizard URL that runs the preview of a stored source again (used by "Review changes" on the team page). */
export const rerunHref = (teamId: string, s: { source: string; url: string; season_id: string | null; competition_label: string; include_past?: boolean }) => {
	const q = new URLSearchParams({ source: s.source, url: s.url, run: '1' });
	if (s.season_id) q.set('season', s.season_id);
	if (s.competition_label) q.set('label', s.competition_label);
	if (s.include_past) q.set('past', '1');
	return `/teams/${teamId}/import?${q}`;
};
