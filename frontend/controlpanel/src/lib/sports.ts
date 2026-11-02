export type Sport = { id: string; name: string; slug: string; club_count: number; competition_count: number; referee_type_count: number };
export type RefereeKind = 'referee' | 'official';
export type RefereeType = { id: string; sport_id: string; kind: RefereeKind; name: string; ordering: number };
export type Competition = { id: string; name: string; module: string; sport_id: string | null; sport_name: string | null; feature_code: string | null };

/** The pseudo-selection for competitions that have no sport. */
export const NO_SPORT = 'none';

export const sportInUse = (s: Sport) => s.club_count > 0 || s.competition_count > 0;
