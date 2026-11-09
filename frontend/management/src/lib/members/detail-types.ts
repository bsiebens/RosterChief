/** Shapes of the members API used by the detail / form / history pages (see api/schemas_members.py). */
export type ShellMe = { id: string; email: string; is_club_admin: boolean; can_manage_members: boolean; can_manage_settings: boolean; can_execute_evaluations: boolean; can_manage_evaluations: boolean; member_id: string | null; managed_team_ids: string[]; admin_group_ids?: string[]; permissions: string[] };

export type MemberFamily = { id: string; name: string; role: string };
export type ClubMembership = {
	id: string;
	season_id: string;
	status: string;
	fee_status?: string | null;
	fee_amount?: string | null;
	amount_paid?: string | null;
	signed_up_at?: string | null;
	activated_at?: string | null;
};
export type StaffAssignment = { id: string; team_id: string; team_name: string; season_id: string; position: string };
export type TeamMembership = { id: string; team_id: string; team_name: string; season_id: string; position: string | null; jersey_number: number | null };

export type MemberDetail = {
	id: string;
	first_name: string;
	last_name: string;
	email: string;
	date_of_birth: string | null;
	language: string;
	license_number: string | null;
	photo_url: string | null;
	profile_photo_url?: string | null;
	player_photo_url?: string | null;
	roles: string[];
	family_ids: string[];
	has_login: boolean;
	photo_public_consent: boolean;
	phone: string | null;
	emergency_phone: string | null;
	/** Same numbers formatted with spaces by the API; for display only (links and forms use the compact value). */
	phone_display?: string | null;
	emergency_phone_display?: string | null;
	families: MemberFamily[];
	groups: { id: string; name: string }[];
	club_memberships: ClubMembership[];
	staff_assignments: StaffAssignment[];
	team_memberships: TeamMembership[];
};

export type Season = { id: string; name: string; start_date: string; end_date: string; is_current: boolean };
export type FamilyMember = { member_id: string; first_name: string; last_name: string; role: string; email?: string; has_login?: boolean };
export type FamilyDetail = { id: string; name: string; display_name: string; members: FamilyMember[] };
/** A guardian of this (child) member, with the contact details the viewer may see. */
export type GuardianContact = { id: string; name: string; phone: string | null; emergency_phone: string | null; phone_display?: string | null; emergency_phone_display?: string | null };

export type HistoryEntry = {
	id: string;
	created: string;
	actor_id: string | null;
	actor_email: string | null;
	member_id: string | null;
	action: string;
	target_type: string;
	target_id: string;
	target_label: string;
	changes: Record<string, { from?: unknown; to?: unknown } | unknown>;
};

export type FormValues = {
	first_name: string;
	last_name: string;
	email: string;
	phone: string;
	emergency_phone: string;
	date_of_birth: string;
	language: string;
	license_number: string;
	photo_public_consent: boolean;
};
