export type PlatformLevel = 'staff' | 'superuser' | null;

export type UserRow = {
	id: string;
	email: string;
	first_name: string;
	last_name: string;
	is_active: boolean;
	last_login: string | null;
	date_joined: string;
	email_verified: boolean;
	mfa_enabled: boolean;
	platform_level: PlatformLevel;
	club_count: number;
	blocked: boolean;
};

export type UserQuery = { q: string; status: string; mfa: string; level: string; page: number };

export type BlockInfo = { is_blocked: true; blocked_at: string | null; blocked_by_email: string | null; reason: string | null };

export type UserAuthenticator = {
	type: 'totp' | 'webauthn' | 'recovery_codes';
	name: string | null;
	created_at: string;
	last_used_at: string | null;
	unused_codes: number | null;
};

export type UserClub = {
	club: { id: string; name: string; slug: string; archived: boolean };
	roles: string[];
	is_owner: boolean;
	is_admin: boolean;
};

export type UserActivity = {
	id: string;
	created: string;
	action: string;
	actor_email: string | null;
	target_type: string;
	target_id: string;
	data: Record<string, unknown>;
};

export type UserDetail = {
	account: {
		id: string;
		email: string;
		first_name: string;
		last_name: string;
		is_active: boolean;
		date_joined: string;
		last_login: string | null;
		has_usable_password: boolean;
		platform_level: PlatformLevel;
		emails: { email: string; verified: boolean; primary: boolean }[];
	};
	blocked: BlockInfo | null;
	mfa: { enabled: boolean; authenticators: UserAuthenticator[] };
	clubs: UserClub[];
	sessions: { active_count: number; last_seen: string | null; truncated: boolean };
	recent_activity: UserActivity[];
};

export const userName = (u: { first_name: string; last_name: string }) => `${u.first_name} ${u.last_name}`.trim();

export const AUTHENTICATOR_LABEL: Record<UserAuthenticator['type'], string> = {
	totp: 'Authenticator app',
	webauthn: 'Security key or passkey',
	recovery_codes: 'Recovery codes'
};

/** Error text for a field, whether the API keys it "reason" or "payload.reason". */
export const fieldError = (fields: Record<string, string>, key: string) => fields[key] ?? fields[`payload.${key}`];
