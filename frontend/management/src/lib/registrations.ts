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
	/** What was bought: lines of one product share a per-registration discount. */
	product_id: string | null;
	product_name: string | null;
	requested_team_id: string | null;
	requested_team_name: string | null;
	requested_position_name: string | null;
	requested_jersey_number: number | null;
	/** reserved (held while the registration waits) | unavailable (cannot be given any more) | assigned | dropped (taken before the team was placed) | null */
	number_state?: 'reserved' | 'unavailable' | 'assigned' | 'dropped' | null;
	base_price: string;
	/** This line's own discount (a per-person tier); the per-registration discount is its own entry in `order_discounts`. */
	discount_amount: string;
	price_override: string | null;
	excluded: boolean;
	/** What the line shows: the override, else base minus its own discount; a credit line (negative base price): its negative amount. */
	price: string;
	/** A line of a negative-priced product: not charged, it takes money off the other lines. */
	is_credit: boolean;
	/** Internal shares (of the credits and of the product's per-registration discount) that keep each fee right; not shown on the line. */
	credit_share: string;
	order_discount_share: string;
	/** What the person's membership costs before the manual discount (price less both shares, never below 0). */
	payable: string;
	fee_amount: string;
	early_payment_deadline: string | null;
	early_payment_discount: string;
};
export type OrderDiscount = { product_id: string | null; name: string; amount: string; people: number; percent?: string | null };
export type RegistrationDetail = RegistrationItem & {
	/** Right after confirming: ids of the lines whose requested number could not be given. */
	numbers_dropped?: string[];
	season_id: string;
	lines: RegistrationLine[];
	/** The separate multi-registration discount lines (positive amounts). */
	order_discounts: OrderDiscount[];
	order_discount_total: string;
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
export type ReviewIn = { lines: { id: string; price: number | null; excluded: boolean; /** Players only: where they are placed when the invoice is confirmed (null: no team). */ requested_team_id?: string | null }[]; manual_discount_amount: number; manual_discount_note: string };
export type RegistrationSettings = { is_open: boolean; intro_text: string; terms_text: string; payment_instructions: string; invoice_due_days: number };

export const REGISTRATION_TABS = ['awaiting', 'confirmed', 'cancelled', 'all'] as const;
export type RegistrationTab = (typeof REGISTRATION_TABS)[number];

export const statusBadge = (s: RegistrationStatus) => ({ awaiting: 'badge-warning', confirmed: 'badge-success', cancelled: 'badge-neutral' })[s] ?? '';
export const registrationHref = (id: string) => `/registrations/${id}`;
export const invoicePdfHref = (id: string) => `/api/v1/registrations/${id}/invoice.pdf`;
