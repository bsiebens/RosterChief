/** The public registration pages (`/join`, `/join/status/{token}`): the shapes of GET /public/registration and the status page, no login. */
export type ClubBrand = { name: string; logo_url: string | null };
export type Ref = { id: string; name: string };
export type VariantOffer = { id: string; name: string; price: string };
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
export type PublicRegistration = { club: ClubBrand; is_open: boolean; intro_text: string; terms_text: string; products: ProductOffer[]; teams: Ref[]; positions: Ref[]; max_entries: number };

export type PreviewLine = { index: number; description: string; base_price: string; discount: string; price: string; early_bird_discount: string; early_bird_deadline: string | null };
export type Preview = { season: Ref; lines: PreviewLine[]; total: string; total_if_early: string; early_bird_discount: string };

export type ChecklistItem = { requirement_id: string; name: string; description: string; requires_document: boolean; state: string; display: 'missing' | 'complete' | 'expiring_soon' | 'expired' | 'bypassed'; valid_until: string | null; has_document: boolean; can_upload: boolean };
export type Fee = { fee_amount: string; amount_paid: string; amount_due: string; remaining_balance: string; fee_status: string; early_bird: { deadline: string; discount: string; fee_if_paid_in_time: string; applies: boolean } | null };
export type PersonStatus = { member_id: string; name: string; entry_kind: string; description: string; requested_team: string | null; membership_status: string; fee: Fee | null; checklist: ChecklistItem[] };
export type RegistrationStatus = { status: 'awaiting' | 'confirmed' | 'cancelled'; season_name: string; submitted_at: string; invoice_number: string | null; invoice_due_date: string | null; people: PersonStatus[]; club: ClubBrand };

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
	requested_position_id: string;
	requested_jersey_number: string;
	photo_public_consent: boolean;
};
let counter = 0;
export const blankEntry = (variant = ''): EntryDraft => ({ uid: ++counter, who: 'child', first_name: '', last_name: '', date_of_birth: '', email: '', kind: 'player', variant_id: variant, requested_team_id: '', requested_position_id: '', requested_jersey_number: '', photo_public_consent: false });

/** An entry as POST /public/registration (and /preview) takes it. */
export const entryBody = (e: EntryDraft) => ({
	who: e.who,
	first_name: e.who === 'child' ? e.first_name.trim() : '',
	last_name: e.who === 'child' ? e.last_name.trim() : '',
	date_of_birth: e.who === 'child' && e.date_of_birth ? e.date_of_birth : null,
	email: e.email.trim(),
	kind: e.kind,
	variant_id: e.variant_id,
	requested_team_id: e.requested_team_id || null,
	requested_position_id: e.requested_position_id || null,
	requested_jersey_number: e.requested_jersey_number.trim() ? Number(e.requested_jersey_number) : null,
	photo_public_consent: e.photo_public_consent
});

export const statusHref = (token: string) => `/join/status/${token}`;
