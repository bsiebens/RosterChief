/** The public registration pages (`/join`, `/join/status/{token}`): the shapes of GET /public/registration and the status page, no login. */
import type { Installment } from '#lib/registrations.ts';

export type ClubBrand = { name: string; logo_url: string | null };
export type Ref = { id: string; name: string };
/** One thing to register for: a variant, or the product itself (as is) when it has no variants. `option_id` is what the form sends back. */
export type VariantOffer = { id: string; option_id: string; name: string; price: string; /** The team registrants for this option are placed in (fixed: the registrant cannot change it); null: they may ask for a team. */ team_id: string | null; team_name: string | null };
export type ProductOffer = {
	id: string;
	name: string;
	description: string;
	season_id: string;
	season_name: string;
	/** "player" / "volunteer": offered only for that kind of registration (a system product category); null: offered for both. */
	entry_kind: 'player' | 'volunteer' | null;
	variants: VariantOffer[];
	early_bird: { discount_type: string; discount_amount: string; deadline: string } | null;
	registrant_discount_scope: string;
	registrant_tiers: { min_registrants: number; discount_type: string; discount_amount: string }[];
};
export type PublicRegistration = { club: ClubBrand; is_open: boolean; intro_text: string; terms_text: string; products: ProductOffer[]; teams: Ref[]; positions: Ref[]; max_entries: number; installment_offsets: number[] };

/** GET /public/registration/numbers: `pool` and `numbers` are null for a team without a number pool (any number); `taken` lists the numbers not free. */
export type NumbersInfo = { pool: { name: string; min: number; max: number } | null; numbers: number[] | null; taken: number[] };

export type PreviewLine = { index: number; description: string; base_price: string; discount: string; price: string; is_credit: boolean; payable: string; credit_share: string; order_discount_share: string; early_bird_discount: string; early_bird_deadline: string | null };
export type OrderDiscount = { description: string; amount: string; people: number; percent?: string | null };
export type Preview = { season: Ref; lines: PreviewLine[]; order_discounts: OrderDiscount[]; order_discount_total: string; total: string; credit_total: string; credit_applied: string; total_if_early: string; early_bird_discount: string };

/** A checklist item is a document (`can_upload` says a file is wanted) or a question (`can_answer`: it can be answered, or the registrant's own answer changed). */
export type ChecklistItem = {
	requirement_id: string;
	name: string;
	description: string;
	kind: 'document' | 'question';
	requires_document: boolean;
	state: string;
	display: 'missing' | 'complete' | 'expiring_soon' | 'expired' | 'bypassed';
	valid_until: string | null;
	has_document: boolean;
	can_upload: boolean;
	question_type: string;
	options: (string | { value: string; label: string })[] | null;
	min_value: string | null;
	max_value: string | null;
	answer: unknown;
	can_answer: boolean;
};
export type Fee = { fee_amount: string; amount_paid: string; amount_due: string; remaining_balance: string; fee_status: string; payment_allows_activation: boolean; early_bird: { deadline: string; discount: string; fee_if_paid_in_time: string; applies: boolean } | null };
export type PersonStatus = { member_id: string; name: string; entry_kind: string; description: string; is_credit: boolean; credit: string | null; requested_team: string | null; membership_status: string; fee: Fee | null; next_installment: Installment | null; checklist: ChecklistItem[] };
export type InvoiceLine = { kind: 'person' | 'order_discount' | 'credit' | string; name: string; description: string; amount: string; percent?: string | null };
export type InvoiceSummary = { lines: InvoiceLine[]; manual_discount_amount: string; manual_discount_note: string; credit_unused: string; total: string };
/** The early-bird discount while it still applies (gone once the deadline has passed or everything is paid): `amount_due` is the invoice total with it. */
export type EarlyBird = { deadline: string; discount: string; amount_due: string; balance: string };
export type RegistrationStatus = { status: 'awaiting' | 'confirmed' | 'cancelled'; season_name: string; submitted_at: string; invoice_number: string | null; invoice_due_date: string | null; /** Only once the invoice is confirmed: a line per person, separate multi-registration discount and credit lines, a manual discount and the total. */ invoice: InvoiceSummary | null; early_bird: EarlyBird | null; people: PersonStatus[]; club: ClubBrand; installment_offsets: number[]; installments: Installment[] };

export type EntryDraft = {
	uid: number;
	who: 'child' | 'self';
	first_name: string;
	last_name: string;
	date_of_birth: string;
	email: string;
	kind: 'player' | 'volunteer';
	variant_id: string;
	requested_team_id: string;
	/** The chosen option fixes the team (`requested_team_id` is then set from it and can't be changed by the registrant). */
	team_fixed: boolean;
	requested_position_id: string;
	requested_jersey_number: string;
	photo_public_consent: boolean;
};
let counter = 0;
export const blankEntry = (variant = ''): EntryDraft => ({ uid: ++counter, who: 'child', first_name: '', last_name: '', date_of_birth: '', email: '', kind: 'player', variant_id: variant, requested_team_id: '', team_fixed: false, requested_position_id: '', requested_jersey_number: '', photo_public_consent: false });

/** An entry as POST /public/registration (and /preview) takes it. */
export const entryBody = (e: EntryDraft) => ({
	who: e.who,
	first_name: e.who === 'child' ? e.first_name.trim() : '',
	last_name: e.who === 'child' ? e.last_name.trim() : '',
	date_of_birth: e.date_of_birth || null,
	email: e.email.trim(),
	kind: e.kind,
	option_id: e.variant_id,
	requested_team_id: e.requested_team_id || null,
	requested_position_id: e.requested_position_id || null,
	requested_jersey_number: e.kind === 'player' && e.requested_jersey_number.trim() ? Number(e.requested_jersey_number) : null,
	photo_public_consent: e.photo_public_consent
});

export const statusHref = (token: string) => `/join/status/${token}`;

/** The free jersey numbers of a team for a new player (aborts with the given signal). Nothing about who holds a number is ever returned. */
export async function fetchNumbers(params: Record<string, string | undefined>, signal: AbortSignal): Promise<{ ok: boolean; data?: NumbersInfo }> {
	const query = new URLSearchParams(Object.entries(params).filter((kv): kv is [string, string] => !!kv[1]));
	try {
		const res = await fetch(`/api/v1/public/registration/numbers?${query}`, { headers: { accept: 'application/json' }, signal });
		return res.ok ? { ok: true, data: (await res.json()) as NumbersInfo } : { ok: false };
	} catch {
		return { ok: false };
	}
}
