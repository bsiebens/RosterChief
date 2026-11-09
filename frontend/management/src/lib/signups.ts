/** The sign-up queue (`/signups`): pending season memberships waiting for approval. Needs `members.manage_members`. */
export type SignupRow = {
	membership_id: string;
	member: { id: string; name: string; email: string; date_of_birth: string | null; has_login: boolean };
	season_id: string;
	season_name: string;
	fee_status: 'unpaid' | 'partially_paid' | 'paid' | 'waived';
	fee_amount: string;
	amount_paid: string;
	remaining_balance: string;
	signed_up_at: string | null;
	/** Names of the active requirements not completed or bypassed yet. */
	open_requirements: string[];
	/** Fee settled and nothing open: can be approved now. */
	clean: boolean;
	teams: { id: string; name: string }[];
	/** Present when the sign-up came from the public registration page. */
	registration: SignupRegistration | null;
};
export type SignupRegistration = {
	registration_id: string;
	entry_kind: string;
	invoice_number: string | null;
	contact_name: string | null;
	contact_email: string | null;
	requested_team_id: string | null;
	requested_team_name: string | null;
	requested_position_id: string | null;
	requested_position_name: string | null;
	requested_jersey_number: number | null;
	product: string;
};
export type Duplicate = { member_id: string; name: string; email: string; date_of_birth: string | null; same_email: boolean; has_membership: boolean };
export type SignupList = { count: number; clean_count: number; season_id: string | null; season_name: string | null; results: SignupRow[] };
export type SignupOptions = { teams: { id: string; name: string }[]; positions: { id: string; name: string }[] };

export const SIGNUP_FILTERS = ['all', 'clean', 'open'] as const;
export type SignupFilter = (typeof SIGNUP_FILTERS)[number];

export const feeBadge = (status: SignupRow['fee_status']) => ({ paid: 'badge-success', waived: 'badge-info', partially_paid: 'badge-warning', unpaid: 'badge-error' })[status] ?? '';
export const documentsHref = (memberId: string) => `/members/${memberId}?tab=documents`;
