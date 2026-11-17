export type LocationDetail = { name: string; address: string; zip_code: string; city: string; latitude: number | null; longitude: number | null };
export type PortalEventDisplay = {
	event_id: string;
	title: string;
	kind: string;
	start: string;
	end: string | null;
	gathering: string | null;
	deadline: string | null;
	location: string;
	/** Where exactly: street address and map pin, so maps opens the address itself and not the town. */
	location_detail?: LocationDetail | null;
	/** The other team's name, for the initials crest when it has no logo. */
	opponent?: string;
	opponent_logo: string | null;
	is_home: boolean;
	score_for: number | null;
	score_against: number | null;
	cancelled: boolean;
	/** Written by staff for the people invited (plain text). */
	notes?: string;
};

export type PortalEvent = PortalEventDisplay & {
	id: string;
	member_id: string;
	status: string;
	note: string;
	/** Why this person cannot sign up right now, so the app says so instead of offering buttons: not invited any more, or an overdue instalment (they can still decline). */
	blocked?: 'not_invited' | 'late_installment' | null;
	/** How many invited people are going (counts only), for the agenda rows. */
	going_count?: number | null;
	invited_count?: number | null;
	/** For not_invited: an open requirement blocks this kind of event (with its names), an exclusion, or the person left the event's team or group. */
	blocked_detail?: { reason: 'requirements' | 'excluded' | 'not_in_audience'; requirements: string[] } | null;
};

export type OfficiatingOpportunity = PortalEventDisplay & {
	member_id: string;
	role: 'referee' | 'official';
	status: 'invited' | 'signed_up' | 'declined' | 'assigned';
	signup_open: boolean;
};

export type Person = { id: string; name: string };
export type NewsTranslation = { title: string; excerpt: string; body: string };
export type PortalArticle = {
	id: string;
	published_at: string;
	teams: string[];
	translations: { nl: NewsTranslation; en: NewsTranslation };
	photos: { url: string; is_main: boolean; object_position: string }[];
};
export type ParentHome = {
	viewer: string;
	shop_enabled: boolean;
	season: string | null;
	event_background: string | null;
	people: (Person & { first_name: string; photo_url: string | null })[];
	events: PortalEvent[];
	officiating: OfficiatingOpportunity[];
	news: PortalArticle[];
	/** Tasks of the family's coming events that still need someone and that this person has not taken. */
	tasks?: OpenTask[];
	sponsors: { id: string; name: string; url: string | null; logo_url: string | null }[];
	fees: { member_id: string; season: string; paid: string; balance: string; next_installment: { due_date: string; remaining: string; late: boolean } | null }[];
	invoices: { id: string; number: string; due: string | null }[];
	/** Invoices the club wrote for an amount that is not a membership fee or a shop item (a fine, a late fee): sent, not cancelled. */
	charges?: { id: string; number: string; member_id: string; summary: string; total: string; paid: string; due: string; due_date: string | null; status: 'sent' | 'partially_paid' | 'overdue' | 'paid' }[];
	/** How the club wants fees paid (its own text), the latest payments received and the forms already sent in. */
	payment_instructions?: string;
	payments?: { id: string; member_id: string; season: string; amount: string; method: string; paid_at: string }[];
	submitted_forms?: { id: string; title: string; member_id: string; submitted_at: string }[];
	forms: { id: string; title: string; member_id: string; opens_at: string | null; closes_at: string | null }[];
};

export type CoachEvent = {
	id: string; title: string; start: string; end: string | null; gathering: string | null; location: string; location_detail?: LocationDetail | null; kind: string; deadline: string | null;
	team_ids: string[]; opponent: string; opponent_logo: string | null; is_home: boolean; notes?: string;
	going: number; out: number; unanswered: number; maybe: number; invited: number;
	cancelled: boolean; lineup: boolean; lineup_published: boolean; has_game_sheet: boolean; can_manage_attendance: boolean;
};

export type CoachHome = {
	season: string | null;
	event_background: string | null;
	teams: Person[];
	can_create_events: boolean;
	can_add_news: boolean;
	news: PortalArticle[];
	events: CoachEvent[];
};

export type ProfilePerson = {
	id: string;
	is_self: boolean;
	name: string;
	first_name: string;
	last_name: string;
	email: string;
	date_of_birth: string | null;
	phone: string;
	emergency_phone: string;
	photo_consent: boolean;
	photo_url: string | null;
	teams: { id: string; name: string; season: string; jersey_number: number | null; captain: boolean }[];
};

export type NotificationSettings = {
	master: Record<'push' | 'email', boolean>;
	categories: { key: string; label: string; push: boolean; email: boolean }[];
	email_mode: 'always' | 'unless_app';
	quiet_hours: { enabled: boolean; start: string; end: string; timezone: string } | null;
};

export type PortalTask = { id: string; title: string; description: string; needed: number; claimed: number; open_slots: number; claimed_by_me: boolean; claimed_by: string[] };
export type EventExtras = {
	lineup: { players: { id: string; name: string; number: number | null; mine: boolean }[]; going: number; out: number; waiting: number } | null;
	/** The game as the competition's page captured it (played games only): lineup and timeline by period. */
	game: GameSheet | null;
	/** Everyone invited and whether they come. Never a reason or note; those are for the coaching app. */
	roster?: { id: string; name: string; number: number | null; position: string | null; extra: boolean; team: string | null; state: 'going' | 'out' | 'maybe' | 'waiting'; /** Once the line-up is published: picked or not (never the lines). */ selected?: boolean | null; mine: boolean }[] | null;
	/** Whole-club events only: the counts instead of a list of names (too many people). */
	totals?: { going: number; maybe: number; waiting: number; out: number; total: number } | null;
	tasks: PortalTask[];
	/** Only for games: whether the person follows it, directly or through a team. */
	follow?: FollowState | null;
	attachments: { id: string; name: string; size: number; download_url: string }[];
};
export type OpenTask = { id: string; event_id: string; event_title: string; start: string; title: string; open_slots: number };

export type GameSheetRow = { time: string; type: string; ours: boolean | null; team: string; icon: string; kind: string; who: string; detail: string; tag: string; score: string; serious: boolean };
export type GameSheetPlayer = { pos: string; number: number | null; name: string; member_id?: string | null; photo_url?: string | null; initials: string; goals?: number; assists?: number; penalty_minutes?: number };
export type GameSheet = {
	ours_label: string;
	theirs_label: string;
	score: string;
	final: boolean;
	updated_at: string | null;
	period_scores: { label: string; score: string }[];
	periods: { label: string; title: string; score: string; rows: GameSheetRow[] }[];
	lineup: { ours_label: string; theirs_label: string; ours: GameSheetPlayer[]; theirs: GameSheetPlayer[] } | null;
};

/** One event of the member calendar with what the family answered to it and any request to referee or officiate. */
export type CalendarEntry = { event: PortalEventDisplay; attendance: PortalEvent[]; officiating: OfficiatingOpportunity[] };

/** A game the person follows (directly or through a team), for the cards on top of Home. */
export type FollowedGame = PortalEventDisplay & { status: 'live' | 'scheduled' | 'final'; teams: string; via: string[] };
export type FollowState = { directly: boolean; via_team: boolean };

export type SubmittedForm = { title: string; member_id: string; submitted_at: string; answers: { label: string; value: unknown }[] };

export type TeamFollows = { teams: { id: string; name: string; mine: boolean; followed: boolean }[]; games: { event_id: string; title: string; start: string }[] };

export type RequirementItem = {
	id: string;
	name: string;
	description: string;
	kind: 'document' | 'question';
	question_type: string;
	options: (string | { value: string; label: string })[] | null;
	requires_document: boolean;
	valid_for_months: number | null;
	state: 'missing' | 'pending' | 'declined' | 'requested' | 'complete' | 'expiring_soon' | 'expired' | 'bypassed';
	valid_until: string | null;
	answer: unknown;
	locked: boolean;
	submitted_at: string | null;
	has_document: boolean;
	completed_at: string | null;
	/** The reason (declined) or message (requested) from the club. */
	review_note: string;
	/** A personalised file the club sent for this person (download through `sentFileHref`). */
	has_sent_file: boolean;
	/** The club has to send its document first; until then the member cannot hand anything in. */
	awaiting_file: boolean;
	needs_staff_file: boolean;
	/** What the person completing it has to do. */
	member_instructions: string;
	sent_at: string | null;
	sent_note: string;
	/** What the member may read of the history: decisions and messages from the club. */
	history: { kind: 'approved' | 'declined' | 'requested' | 'sent_file'; note: string; created: string }[];
};
export const sentFileHref = (memberId: string, requirementId: string) => `/api/v1/portal/me/requirements/${memberId}/${requirementId}/sent-file`;
export type MembershipRow = {
	member_id: string;
	season: string;
	status: string;
	fee_status: string;
	fee: string;
	paid: string;
	balance: string;
	invoice_id: string | null;
	invoice_number: string | null;
	requirements: RequirementItem[];
};
export type PortalVoucher = { id: string; number: string; member_id: string | null; amount: string; available: string; expiry_date: string; expired: boolean };
export type PortalOverview = { memberships: MembershipRow[]; vouchers: PortalVoucher[]; forms: { id: string; title: string; member_id: string; closes_at: string | null }[] };

/** An evaluation the club has shared with the family: `notes` carries only the public note, `full` adds every score and answer. */
export type SharedEvaluation = {
	id: string;
	member_id: string;
	checklist: string;
	season: string;
	released_at: string | null;
	release: 'notes' | 'full';
	public_note: string;
	answers: { label: string; field_type: string; section: string; options: unknown; value: unknown }[];
};

/** Personal statistics: who has them here, and whether their public link is switched on (`share_token` set). */
export type StatsPerson = { id: string; name: string; first_name: string; photo_url: string | null; is_self: boolean; share_token: string | null };
export type StatsPeople = { available: boolean; people: StatsPerson[] };
