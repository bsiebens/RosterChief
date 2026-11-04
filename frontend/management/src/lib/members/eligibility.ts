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
