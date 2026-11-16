export type LineupSlot = { key: string; label: string };
export type LineupGroup = { key: string; label: string; slots: LineupSlot[] };
export type Sport = {
	id: string;
	name: string;
	slug: string;
	club_count: number;
	competition_count: number;
	referee_type_count: number;
	lineup_slots_enabled: boolean;
	lineup_template: LineupGroup[];
};

/** A sensible ice hockey template: two goalies, four lines of five, then the special teams (a player may be in a line and in a special team). */
export const HOCKEY_PRESET: LineupGroup[] = [
	{ key: 'goalies', label: 'Goalies', slots: [{ key: 'g1', label: 'G1' }, { key: 'g2', label: 'G2' }] },
	...[1, 2, 3, 4].map((n) => ({
		key: `line${n}`,
		label: `Line ${n}`,
		slots: ['LW', 'C', 'RW', 'LD', 'RD'].map((l) => ({ key: l.toLowerCase(), label: l }))
	})),
	...[1, 2].map((n) => ({
		key: `pp${n}`,
		label: `Power play ${n}`,
		slots: ['C', 'LW', 'RW', 'LD', 'RD'].map((l) => ({ key: l.toLowerCase(), label: l }))
	})),
	...[1, 2].map((n) => ({
		key: `pk${n}`,
		label: `Penalty kill ${n}`,
		slots: ['F1', 'F2', 'D1', 'D2'].map((l) => ({ key: l.toLowerCase(), label: l }))
	})),
	{ key: 'extra', label: 'Extra attacker', slots: ['A1', 'A2', 'A3'].map((l) => ({ key: l.toLowerCase(), label: l })) }
];
export type RefereeKind = 'referee' | 'official';
export type RefereeType = { id: string; sport_id: string; kind: RefereeKind; name: string; ordering: number };
export type Competition = { id: string; name: string; module: string; sport_id: string | null; sport_name: string | null; feature_code: string | null };

/** The pseudo-selection for competitions that have no sport. */
export const NO_SPORT = 'none';

export const sportInUse = (s: Sport) => s.club_count > 0 || s.competition_count > 0;
