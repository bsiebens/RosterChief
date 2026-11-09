/** Registrations (`/registrations`, `/registration/settings`): the staff billing review of public registrations. Needs `members.manage_members`. */
export type RegistrationStatus = 'awaiting' | 'confirmed' | 'cancelled';
export type PersonRef = { id: string; name: string; email: string };
export type RegistrationItem = {
	id: string;
	status: RegistrationStatus;
	contact: PersonRef | null;
	season_name: string;
	submitted_at: string;
	people_count: number;
	total: string;
	invoice_number: string | null;
	confirmed_at: string | null;
};
export type RegistrationList = { count: number; awaiting_count: number; results: RegistrationItem[] };
export type RegistrationLine = {
	id: string;
	member: PersonRef;
	membership_id: string;
	entry_kind: 'player' | 'volunteer' | string;
	description: string;
	requested_team_name: string | null;
	requested_position_name: string | null;
	requested_jersey_number: number | null;
	base_price: string;
	discount_amount: string;
	price_override: string | null;
	excluded: boolean;
	/** Before the credits and the manual discount; a credit line (negative base price): its negative amount. */
	price: string;
	/** A line of a negative-priced product: not charged, it takes money off the other lines. */
	is_credit: boolean;
	credit_share: string;
	/** What the person pays before the manual discount (price less the credit share, never below 0). */
	payable: string;
	fee_amount: string;
	early_payment_deadline: string | null;
	early_payment_discount: string;
};
export type RegistrationDetail = RegistrationItem & {
	season_id: string;
	lines: RegistrationLine[];
	subtotal: string;
	credit_total: string;
	credit_applied: string;
	manual_discount_amount: string;
	manual_discount_note: string;
	invoice_due_date: string | null;
	invoice_sent_at: string | null;
	confirmation_sent_at: string | null;
	cancelled_at: string | null;
	status_path: string;
};
/** What PATCH /registrations/{id} takes: a number overrides a line's price, `null` goes back to the computed one. */
export type ReviewIn = { lines: { id: string; price: number | null; excluded: boolean }[]; manual_discount_amount: number; manual_discount_note: string };
export type RegistrationSettings = { is_open: boolean; intro_text: string; terms_text: string; payment_instructions: string; invoice_due_days: number };

export const REGISTRATION_TABS = ['awaiting', 'confirmed', 'cancelled', 'all'] as const;
export type RegistrationTab = (typeof REGISTRATION_TABS)[number];

export const statusBadge = (s: RegistrationStatus) => ({ awaiting: 'badge-warning', confirmed: 'badge-success', cancelled: 'badge-neutral' })[s] ?? '';
export const registrationHref = (id: string) => `/registrations/${id}`;
export const invoicePdfHref = (id: string) => `/api/v1/registrations/${id}/invoice.pdf`;
