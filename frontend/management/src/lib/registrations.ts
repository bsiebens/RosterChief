/** Registrations (`/registrations`, `/registration/settings`): the staff billing review of public registrations and the finance page (balances,
 * payments, invoices). The registrations endpoints need `registration.manage_finance`; the settings need `members.manage_members`. */
export type RegistrationStatus = 'awaiting' | 'confirmed' | 'cancelled';
export type PersonRef = { id: string; name: string; email: string };
export type Installment = { due_date: string; amount: string; remaining: string; late: boolean };
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
	invoice_due_date: string | null;
	/** Finance: what the invoice comes to (fees less early-payment discounts in force), what was paid and what is left. Zero until confirmed. */
	amount_due: string;
	amount_paid: string;
	balance: string;
	/** null until the invoice is confirmed, and when cancelled. */
	payment_status: PaymentStatus | null;
	installment_offsets: number[];
	next_installment: Installment | null;
	legacy_invoice_only: boolean;
};
export type PaymentStatus = 'unpaid' | 'partial' | 'paid' | 'overdue';
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
	/** Finance: this person's share of the invoice. */
	fee_status: 'unpaid' | 'partially_paid' | 'paid' | 'waived';
	amount_due: string;
	amount_paid: string;
	balance: string;
	next_installment: Installment | null;
};
export type PaymentMethod = 'bank_transfer' | 'cash' | 'card' | 'voucher' | 'other';
export type InvoicePayment = {
	id: string;
	member: PersonRef;
	membership_id: string;
	amount: string;
	method: PaymentMethod | string;
	reference: string;
	paid_at: string;
	note: string;
	recorded_by: string | null;
	voucher_number: string | null;
};
/** What POST /registrations/{id}/payments takes: `allocations` (when present) say how much goes to whom and must add up to `amount`. */
export type PaymentIn = { amount: string; method: PaymentMethod; reference: string; note: string; paid_at: string | null; voucher_number?: string | null; allocations?: { membership_id: string; amount: string }[] };
export type VoucherLookup = { number: string; amount: string; available: string; expiry_date: string; status: 'usable' | 'exhausted' | 'expired' | 'inactive' };
export type OrderDiscount = { product_id: string | null; name: string; amount: string; people: number; percent?: string | null };
export type LegacyInvoice = {
	subtotal: string;
	discount_amount: string;
	manual_discount_amount: string;
	total: string;
	lines: { id: string; member_id?: string; member: string; description: string; price: string; discount_amount: string; excluded_from_invoice: boolean }[];
};
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
	/** Every payment on the invoice, newest first. */
	payments: InvoicePayment[];
	/** The club has the shop, so a payment can be made with a shop voucher. */
	vouchers_enabled: boolean;
	/** The early-bird discount while it still applies; null once passed or paid. */
	early_bird: { deadline: string; discount: string; amount_due: string; balance: string } | null;
	installments: Installment[];
	legacy_invoice_history: LegacyInvoice | null;
};
/** What PATCH /registrations/{id} takes: a number overrides a line's price, `null` goes back to the computed one. */
export type ReviewIn = { lines: { id: string; price: number | null; excluded: boolean; /** Players only: where they are placed when the invoice is confirmed (null: no team). */ requested_team_id?: string | null }[]; manual_discount_amount: number; manual_discount_note: string };
export type RegistrationSettings = { is_open: boolean; intro_text: string; terms_text: string; payment_instructions: string; invoice_due_days: number; installment_offsets: number[]; installment_grace_days: number; installment_block_kinds: string[] };

/** A membership with a fee still open that is not part of a registration (`/finance/open-members`). */
export type OpenMember = { membership_id: string; member: PersonRef; season_name: string; status: string; fee_status: string; fee_amount: string; amount_paid: string; balance: string; early_payment_deadline: string | null };
export type OpenMembers = { count: number; results: OpenMember[]; vouchers_enabled: boolean };
export const PAYMENT_TABS = ['open', 'overdue', 'installments', 'paid', 'all'] as const;
export type PaymentTab = (typeof PAYMENT_TABS)[number];
export const PAYMENT_METHODS: PaymentMethod[] = ['bank_transfer', 'cash', 'card', 'voucher', 'other'];
export const paymentBadge = (s: PaymentStatus | null) => ({ unpaid: 'badge-neutral', partial: 'badge-info', paid: 'badge-success', overdue: 'badge-error' })[s ?? 'unpaid'] ?? '';
export const financeHref = (id: string) => `/finance/${id}`;

export const REGISTRATION_TABS = ['awaiting', 'confirmed', 'cancelled', 'all'] as const;
export type RegistrationTab = (typeof REGISTRATION_TABS)[number];

export const statusBadge = (s: RegistrationStatus) => ({ awaiting: 'badge-warning', confirmed: 'badge-success', cancelled: 'badge-neutral' })[s] ?? '';
export const registrationHref = (id: string) => `/registrations/${id}`;
export const invoicePdfHref = (id: string) => `/api/v1/registrations/${id}/invoice.pdf`;
