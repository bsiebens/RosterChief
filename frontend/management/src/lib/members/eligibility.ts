/** Shapes of the referee / official eligibility API (`GET /members/{id}/officiating`, `/members/officiating/options`). */
export type NamedRef = { id: string; name: string };
export type EligibilityProfile = {
	id: string;
	level: NamedRef | null;
	valid_until: string | null;
	is_valid: boolean;
	is_eligible: boolean;
	eligible_teams: NamedRef[];
	excluded_teams: NamedRef[];
};
export type Officiating = { referee: EligibilityProfile | null; official: EligibilityProfile | null };
export type OfficiatingOptions = { referee_levels: NamedRef[]; official_levels: NamedRef[]; teams: NamedRef[] };
/** Another referee or official whose level and exclusions can be copied onto someone being set up (from `GET /officiating/people`). */
export type CopySource = { member_id: string; name: string; level_id: string | null; level_name: string | null; excluded_team_ids: string[] };
export type CopySources = { referee: CopySource[]; official: CopySource[] };
