export type PortalEventDisplay = {
	event_id: string;
	title: string;
	kind: string;
	start: string;
	end: string | null;
	gathering: string | null;
	deadline: string | null;
	location: string;
	/** The other team's name, for the initials crest when it has no logo. */
	opponent?: string;
	opponent_logo: string | null;
	is_home: boolean;
	score_for: number | null;
	score_against: number | null;
	cancelled: boolean;
};

export type PortalEvent = PortalEventDisplay & {
	id: string;
	member_id: string;
	status: string;
	note: string;
	/** Why this person cannot sign up right now, so the app says so instead of offering buttons: not invited any more, or an overdue instalment (they can still decline). */
	blocked?: 'not_invited' | 'late_installment' | null;
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
	/** How the club wants fees paid (its own text), the latest payments received and the forms already sent in. */
	payment_instructions?: string;
	payments?: { id: string; member_id: string; season: string; amount: string; method: string; paid_at: string }[];
	submitted_forms?: { id: string; title: string; member_id: string; submitted_at: string }[];
	forms: { id: string; title: string; member_id: string; opens_at: string | null; closes_at: string | null }[];
};

export type CoachHome = {
	season: string | null;
	event_background: string | null;
	teams: Person[];
	news: PortalArticle[];
	events: {
		id: string; title: string; start: string; location: string; kind: string; deadline: string | null;
		team_ids: string[]; opponent_logo: string | null; is_home: boolean;
		going: number; out: number; unanswered: number;
		cancelled: boolean; lineup: boolean; lineup_published: boolean; can_manage_attendance: boolean;
	}[];
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
	state: 'missing' | 'complete' | 'expiring_soon' | 'expired' | 'bypassed';
	valid_until: string | null;
	answer: unknown;
	has_document: boolean;
	completed_at: string | null;
};
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
