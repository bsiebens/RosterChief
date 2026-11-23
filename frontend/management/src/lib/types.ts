export type Flow = { id: string; is_pending?: boolean; types?: string[] };

export type ApiError = { message: string; code: string; param?: string };

export type ApiResult<T = any> = {
	status: number;
	ok: boolean;
	data?: T;
	meta?: any;
	flows?: Flow[];
	errors?: ApiError[];
};

export type User = { id: number | string; email: string; display?: string };

/** GET /api/v1/me: the signed-in user inside the club of the current hostname. */
export type Me = {
	id: string;
	email: string;
	/** The user's own member record in this club, if they have one. */
	member_id: string | null;
	/** The account has a club membership or is a parent or guardian of a child. */
	has_member_portal_role: boolean;
	first_name: string;
	last_name: string;
	club: { slug: string; name: string; is_platform: boolean };
	roles: string[];
	permissions: string[];
	is_club_admin: boolean;
	/** members.manage_members (or the Owner role): edit members, families, groups, memberships. */
	can_manage_members: boolean;
	/** registration.manage_finance (or the Owner role): the billing review of registrations, their invoices and the payments against them. */
	can_manage_finance: boolean;
	can_manage_charges: boolean;
	can_manage_sponsors: boolean;
	/** admin.manage_settings (or the Owner role): club settings, levels, positions, pools, creating and ordering teams. */
	can_manage_settings: boolean;
	/** evaluations.execute_evaluations (or the Owner role): give evaluations, run evaluation meetings and see the Evaluations tab on a member. */
	can_execute_evaluations: boolean;
	/** evaluations.manage_evaluations (or the Owner role): build the evaluation checklists. */
	can_manage_evaluations: boolean;
	/** authorizations.manage_roles (or the Owner role): create roles, set their permissions and give them to people. */
	can_manage_roles: boolean;
	can_manage_referees: boolean;
	/** news.publish_newsitems (or the Owner role): edit every news item and publish, unpublish and send back. */
	can_publish_news: boolean;
	/** Any club member with a login may add news (and edit their own while it is a draft or pending review). */
	can_add_news: boolean;
	/** forms.manage_forms (or the Owner role): build forms, send them to members and read the responses. */
	can_manage_forms: boolean;
	/** Shop permissions (false without the `shop` feature): products, inventory, orders, discounts and vouchers, invoices. */
	can_manage_shop_products: boolean;
	can_manage_shop_inventory: boolean;
	can_manage_shop_orders: boolean;
	can_manage_shop_discounts: boolean;
	can_manage_shop_invoices: boolean;
	/** Tournaments (false without the `tournaments` feature): manage = settings, registrations, payments; operate = teams, rosters, schedule, scores, tasks. Manage implies operate. */
	can_manage_tournaments: boolean;
	can_operate_tournaments: boolean;
	/** Chat (absent or false without the `chat` feature): may use it, may moderate reports, may post in announcement chats. */
	can_use_chat?: boolean;
	can_moderate_chat?: boolean;
	can_announce_chat?: boolean;
	/** The shop's VAT setting: 'included' while shop prices contain VAT, otherwise 'off'. */
	shop_vat_mode: string;
	/** Teams where the user holds a management position this season: they may edit those teams. */
	managed_team_ids: string[];
	/** Groups the user administers: they may schedule events for them and manage their people. */
	admin_group_ids: string[];
	features: string[];
};

/** GET /api/v1/club: public branding of the club behind the current hostname. Colours are validated #RRGGBB or ''. */
export type Branding = {
	slug: string;
	name: string;
	is_platform: boolean;
	logo_url: string | null;
	/** Browser icons the backend generated from the logo (site-relative); empty without a logo. */
	icons: { ico?: string; png_32?: string; png_192?: string; png_512?: string; apple_touch?: string };
	primary_color: string;
	secondary_color: string;
	primary_content_color: string;
	secondary_content_color: string;
};

/** GET /api/v1/my-clubs (bare domain only), plus the absolute URL of the club added by the server. */
export type MyClub = {
	slug: string;
	name: string;
	host: string;
	logo_url: string | null;
	primary_color: string;
	primary_content_color: string;
	url: string;
};

/** Which kind of hostname served the request. 'error' = unknown club or unreachable backend. */
export type HostMode = 'platform' | 'club' | 'error';

export type Authenticator =
	| { type: 'totp'; created_at: number; last_used_at: number | null }
	| {
			type: 'recovery_codes';
			created_at: number;
			last_used_at: number | null;
			total_code_count: number;
			unused_code_count: number;
			unused_codes?: string[];
	  }
	| {
			type: 'webauthn';
			id: number;
			name: string;
			is_passwordless?: boolean;
			created_at: number;
			last_used_at: number | null;
	  };

/** What the server knows about the visitor, computed once per request in hooks.server.ts. */
export type AuthState =
	| { status: 'anonymous' }
	/** Password accepted, second factor still outstanding. */
	| { status: 'mfa_pending'; types: string[] }
	/** Signed in with allauth, but not a user of this club (club mode only). */
	| { status: 'no_access'; user: User }
	/** Club staff without a second factor: only the security screens work. */
	| { status: 'mfa_enrollment_required'; user: User }
	/** `me` is set in club mode; on the bare domain (platform mode) it is null and `locals.myClubs` is used instead. */
	| { status: 'authenticated'; user: User; me: Me | null; memberShopEnabled?: boolean; memberChatEnabled?: boolean };
