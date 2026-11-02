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

export type Me = {
	id: string;
	email: string;
	first_name: string;
	last_name: string;
	level: 'staff' | 'superuser';
	version: string;
};

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
	/** Signed in with allauth, but not a platform staff account. */
	| { status: 'no_access'; user: User }
	/** Platform staff without a second factor: only the security screens work. */
	| { status: 'mfa_enrollment_required'; user: User }
	| { status: 'authenticated'; user: User; me: Me };
