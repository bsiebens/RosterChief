export type PortalEventDisplay = {
	event_id: string;
	title: string;
	kind: string;
	start: string;
	end: string | null;
	gathering: string | null;
	deadline: string | null;
	location: string;
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
	sponsors: { id: string; name: string; url: string | null; logo_url: string | null }[];
	fees: { member_id: string; season: string; paid: string; balance: string; next_installment: { due_date: string; remaining: string; late: boolean } | null }[];
	invoices: { id: string; number: string; due: string | null }[];
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
