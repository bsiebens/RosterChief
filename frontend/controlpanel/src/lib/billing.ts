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
	status: 'active' | 'voided';
	voided_at: string | null;
	void_reason: string;
	replaced_by_number: string | null;
	replaces_number: string | null;
};

export type VoidedInvoice = { number: string; voided_at: string; void_reason: string; replaced_by_number: string | null; credit_note_number: string | null };

export type Payment = { id: string; amount: string; method: string; reference: string; paid_at: string; note: string; recorded_by_email?: string | null };

export type DueState = 'overdue' | 'in_grace' | 'issued_ahead' | 'settled';
export type DueStatus = 'unpaid' | 'partial' | 'paid' | 'waived' | 'cancelled';

export type Due = {
	id: string;
	club: ClubRef;
	plan: PlanRef;
	cycle?: CycleRef | null;
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
	voided_invoices: VoidedInvoice[];
	payments: Payment[];
} & ProrationFields;

export type CreditNoteKind = 'plan_change' | 'void';
export type CreditNoteBrief = { number: string; total: string; kind: CreditNoteKind };

/** Set on a period that a plan change shortened and repriced; amount/vat_amount/total/period_end are then the prorated values. */
export type ProrationFields = {
	prorated: boolean;
	original_amount: string | null;
	original_vat_amount: string | null;
	original_total: string | null;
	original_period_end: string | null;
	prorated_at: string | null;
	credit_transferred: string;
	credit_notes: CreditNoteBrief[];
};

export type CreditNote = {
	id: string;
	number: string;
	issued_at: string;
	club: ClubRef;
	invoice_number: string;
	due_id: string;
	net: string;
	vat: string;
	total: string;
	vat_rate: string;
	reverse_charge: boolean;
	reason: string;
	kind: CreditNoteKind;
	replacement_invoice_number: string | null;
	applied_to: { due_id: string; invoice_number: string | null; amount: string }[];
};

export type VoidReissueResult = { voided: InvoiceBrief; invoice: InvoiceBrief; credit_note: CreditNote | null };

export type CreditEntry = { id: string; amount: string; kind: 'overpayment' | 'cancelled_period' | 'applied'; note: string; created: string; credit_note_number: string | null; due_id: string | null };
export type CreditLedger = { balance: string; entries: CreditEntry[] };

type Money3 = { net: string; vat: string; total: string };

export type PlanChangePreview = {
	invoice_number?: InvoiceNumberEcho;
	club_id: string;
	effective_date: string;
	from_plan: PlanRef | null;
	to_plan: PlanRef;
	from_cycle?: CycleRef | null;
	to_cycle?: CycleRef | null;
	old_period: {
		id: string;
		period_start: string;
		period_end: string;
		new_period_end: string;
		total_days: number;
		used_days: number;
		unused_days: number;
		original: Money3;
		prorated: Money3;
		status_before: string;
		status: string;
		paid: string;
		credit_to_issue: Money3 | null;
		overpayment_to_credit: string;
		has_invoice: boolean;
		invoice_number: string | null;
		is_trial: boolean;
	} | null;
	cancelled_periods: { id: string; period_start: string; period_end: string; total: string; paid: string; invoice_number: string | null }[];
	credit_notes_to_issue: ({ for_invoice: string | null } & Money3)[];
	credit_balance_before: string;
	credit_available_for_new_period: string;
	new_period: {
		period_start: string;
		period_end: string;
		plan: PlanRef;
		cycle?: CycleRef | null;
		net: string;
		vat_rate: string;
		vat_amount: string;
		total: string;
		reverse_charge: boolean;
		grace_until: string;
		invoice_opens: string;
	};
	credit_applied: string;
	amount_payable_after_credit: string;
	credit_balance_after: string;
	warnings: string[];
};

export type PlanChangeResult = PlanChangePreview & {
	created: { old_due_id: string | null; new_due_id: string; new_invoice_id: string | null; new_invoice_number: string | null; credit_note_ids: string[]; cancelled_due_ids: string[] };
	credit_notes: CreditNote[];
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

export type BillingSummary = { owed: string; overdue_count: number; in_grace_count: number; invoices_to_send: number; clubs_unbilled: number; auto_invoicing?: boolean; invoices_needed?: number };

/** A club that needs an invoice made by hand while automatic invoicing is off (`open`: the next period is not opened yet; `invoice`: the period exists without an invoice). */
export type InvoicingNeed = { kind: 'open' | 'invoice'; club: ClubRef; plan: PlanRef; period_start: string; opens_on: string; days: number; amount: string | null; due_id: string | null };
export type InvoicingOverview = { auto_invoicing: boolean; pending_count: number; ahead_days: number; items: InvoicingNeed[] };

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
	/** Read it from a GET; the switch itself is changed with PUT /billing/settings/auto-invoicing. */
	auto_invoicing?: boolean | null;
	invoice_number_pattern: string;
	credit_note_number_pattern: string;
	/** Always null in a GET; send a positive integer on PUT only to move the counter forward. */
	invoice_next_sequence?: number | null;
	credit_note_next_sequence?: number | null;
	// Read-only (ignored on PUT).
	invoice_next_number_preview?: string;
	credit_note_next_number_preview?: string;
	invoice_current_sequence?: number;
	credit_note_current_sequence?: number;
	invoice_counter_resets?: CounterResets;
	credit_note_counter_resets?: CounterResets;
};

export type CounterResets = 'yearly' | 'monthly' | 'never';
export type NumberPatternPreview = { valid: boolean; error: string | null; example: string | null; next: string | null; scope: string | null; scope_label: string | null; resets: CounterResets | null };
export type NumberingPreview = { invoice: NumberPatternPreview; credit_note: NumberPatternPreview; placeholders: { token: string; meaning: string }[]; literal_characters: string; max_length: number };
export type InvoiceNumberEcho = { mode: 'custom' | 'automatic'; number: string | null; next_automatic: string | null };

/** A billing cycle in short: what it is called and how long one period lasts. */
export type CycleRef = { id: string; name: string; duration_months: number };
export type CyclePrice = { id: string; active_from: string; amount: string };
/** One way to be billed on a plan (monthly, quarterly, yearly ...), with its own dated prices. */
export type PlanCycle = {
	id: string;
	plan_id: string;
	/** The label, else Monthly / Quarterly / Yearly / Every N months. */
	name: string;
	label: string;
	duration_months: number;
	renewal_lead_days: number;
	grace_days: number;
	position: number;
	is_active: boolean;
	prices: CyclePrice[];
	/** null = clubs cannot be billed on this cycle (no price yet). */
	current_price: string | null;
	club_count: number;
};
export type BillingPlan = {
	plan: PlanRef;
	is_active: boolean;
	position: number;
	/** In the order the platform chose; the first active one is the default. */
	cycles: PlanCycle[];
	club_count: number;
};
