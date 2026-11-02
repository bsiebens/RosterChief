import type { CreditNote, InvoiceBrief, ProrationFields, VoidedInvoice } from './billing.ts';

export type SportRef = { id: string; name: string; slug: string };
export type Sport = SportRef & { club_count: number };

export type ClubRow = {
	id: string;
	name: string;
	slug: string;
	schema_name: string;
	sport: SportRef | null;
	owner_email: string;
	plan: string | null;
	archived_at: string | null;
	created: string;
	logo_url: string | null;
};

export type ClubAdmin = { id: string; email: string; first_name: string; last_name: string; is_owner: boolean };

export type ClubDetail = ClubRow & {
	legal_name: string;
	contact_email: string;
	website: string;
	legal_address: string;
	legal_zip_code: string;
	legal_city: string;
	legal_country: string;
	legal_country_name: string;
	vat_id: string;
	domains: string[];
	admins: ClubAdmin[];
};

export type Page<T> = { items: T[]; count: number };

export type Provisioning = {
	id: string;
	name: string;
	status: 'pending' | 'running' | 'succeeded' | 'failed';
	club_id: string | null;
	club_slug: string | null;
	error: string;
	created: string;
};

export type PlanRef = { id: string; code: string; name: string };
export type Plan = PlanRef & { description: string; is_active: boolean; is_default: boolean; subscriber_count: number };

export type Subscription = { id: string; plan: PlanRef; status: string; starts_at: string; ends_at: string | null; trial_ends_at: string | null; is_current: boolean };

export type Entitlement = { code: string; name: string; kind: 'flag' | 'limit'; enabled: boolean; limit: number | null; source: 'plan' | 'default_plan' | 'overdue' | 'override' | 'none' };

export type Override = { feature_code: string; feature_name: string; enabled: boolean; limit: number | null; expires_at: string | null; reason: string; is_current: boolean };

export type ClubStats = { plan: string | null; members: number; member_limit: number | null; admins: number; dues_owed: string; features: string[]; downgraded_at: string | null; trial_ends_at: string | null };

export type Due = {
	id: string;
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
	status: string;
	state: 'overdue' | 'in_grace' | 'issued_ahead' | 'settled';
	is_trial: boolean;
	invoice: InvoiceBrief | null;
	voided_invoices?: VoidedInvoice[];
} & ProrationFields;

export type ClubBilling = {
	billed: boolean;
	plan: PlanRef | null;
	auto_renew: boolean | null;
	auto_archive: boolean | null;
	notes: string;
	trial_ends_at: string | null;
	downgraded_at: string | null;
	owed: string;
	credit_balance: string;
	credit_notes: CreditNote[];
	dues: Due[];
};

export const initials = (name: string) =>
	name
		.split(/\s+/)
		.filter(Boolean)
		.slice(0, 2)
		.map((w) => w[0])
		.join('')
		.toUpperCase();

export type Country = { code: string; name: string };

export type StatGroup = { key: string; title: string; icon: string; available: boolean; stats: { label: string; value: number | null }[] };

export type ClubOverview = {
	kpis: { members: number; new_members_30d: number; renewal_rate: number | null; renewal_note: string };
	signups: { month: string; value: string }[];
	member_types: { type: string; label: string; count: number }[];
	groups: StatGroup[];
	home_location: null;
};
