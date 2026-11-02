// Types for the billing API (see controlpanel/api/routers/billing.py). Money values are strings; `amount` is NET,
// `total` = amount + vat_amount, `balance` = total - amount_paid.

export type ClubRef = { id: string; name: string; slug: string };
export type PlanRef = { id: string; code: string; name: string };

export type InvoiceBrief = {
	number: string;
	issued_at: string;
	sent_at: string | null;
	sent_method: '' | 'email' | 'manual';
	custom_number: boolean;
	needs_sending: boolean;
	net: string;
	vat: string;
	total: string;
	vat_rate: string;
	reverse_charge: boolean;
	due_date: string;
	has_snapshot: boolean;
};

export type Payment = { id: string; amount: string; method: string; reference: string; paid_at: string; note: string; recorded_by_email?: string | null };

export type DueState = 'overdue' | 'in_grace' | 'issued_ahead' | 'settled';
export type DueStatus = 'unpaid' | 'partial' | 'paid' | 'waived' | 'cancelled';

export type Due = {
	id: string;
	club: ClubRef;
	plan: PlanRef;
	amount: string;
	vat_rate: string;
	vat_amount: string;
	total: string;
	reverse_charge: boolean;
	amount_paid: string;
	balance: string;
	period_start: string;
	period_end: string;
	grace_until: string;
	status: DueStatus;
	state: DueState;
	is_trial: boolean;
	paid_at: string | null;
	invoice: InvoiceBrief | null;
	payments: Payment[];
};

export type Cycle = {
	club: ClubRef;
	plan: PlanRef;
	current_period_end: string | null;
	next_period_start: string;
	invoice_opens_on: string;
	renewal_lead_days: number;
	opened: boolean;
	invoice_due_now: boolean;
	trial_ends_at: string | null;
	lapsed: boolean;
	downgraded_at: string | null;
};

export type BillingSummary = { owed: string; overdue_count: number; in_grace_count: number; invoices_to_send: number; clubs_unbilled: number };

export type IssuerSettings = {
	legal_name: string;
	address: string;
	zip_code: string;
	city: string;
	country: string;
	vat_id: string;
	iban: string;
	bic: string;
	contact_email: string;
	website: string;
	invoice_footer: string;
	payment_terms: string;
	vat_rate: string;
	archive_after_days: number;
};

export type BillingPlan = {
	plan: PlanRef;
	is_active: boolean;
	terms: { duration_months: number; renewal_lead_days: number; grace_days: number } | null;
	prices: { id: string; active_from: string; amount: string }[];
	current_price: string | null;
	club_count: number;
};
