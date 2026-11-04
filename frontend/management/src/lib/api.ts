import type { ApiResult } from './types';
import { t } from './i18n.svelte.ts';

const AUTH = '/_allauth/app/v1';

type Fetch = typeof fetch;

/** One call to the allauth headless API through this app's proxy, normalised into an ApiResult. */
export async function auth<T = any>(
	method: string,
	path: string,
	body?: unknown,
	opts: { fetch?: Fetch; headers?: Record<string, string> } = {}
): Promise<ApiResult<T>> {
	const f = opts.fetch ?? fetch;
	let res: Response;
	try {
		res = await f(AUTH + path, {
			method,
			headers: { accept: 'application/json', ...(body !== undefined ? { 'content-type': 'application/json' } : {}), ...opts.headers },
			body: body !== undefined ? JSON.stringify(body) : undefined
		});
	} catch {
		return { status: 0, ok: false, errors: [{ code: 'network', message: t('auth.err.network') }] };
	}
	const json = await res.json().catch(() => null);
	if (res.status === 502 && json?.error) {
		return { status: 502, ok: false, errors: [{ code: json.error.code, message: json.error.message }] };
	}
	return {
		status: res.status,
		ok: res.ok,
		data: json?.data,
		meta: json?.meta,
		flows: json?.data?.flows,
		errors: json?.errors
	};
}

/** Field-level error messages keyed by param, plus the ones that belong to the form as a whole. */
export function splitErrors(result: ApiResult): { fields: Record<string, string>; form: string[] } {
	const fields: Record<string, string> = {};
	const form: string[] = [];
	if (result.status === 429) form.push(t('auth.err.too_many'));
	for (const e of result.errors ?? []) {
		if (e.param) fields[e.param] ??= e.message;
		else form.push(e.message);
	}
	if (!form.length && !Object.keys(fields).length && !result.ok && result.status !== 401) {
		form.push(t('auth.err.generic'));
	}
	return { fields, form };
}

export const isPending = (r: ApiResult, id: string) => !!r.flows?.some((f) => f.id === id && f.is_pending !== false);

// --- re-authentication -------------------------------------------------------------------------
// Sensitive security actions (enrolling or removing a factor, viewing recovery codes) answer 401 with a
// "reauthenticate" flow when the session is not fresh. A single dialog in the app shell handles that.

type Reauth = { resolve: (ok: boolean) => void; mfaTypes: string[] };
let handler: ((r: Reauth) => void) | null = null;
export const onReauthRequired = (fn: ((r: Reauth) => void) | null) => (handler = fn);

/** Runs `call`; if the server asks for re-authentication, prompts once and retries. */
export async function withReauth<T>(call: () => Promise<ApiResult<T>>): Promise<ApiResult<T>> {
	const first = await call();
	const needs = first.status === 401 && first.flows?.some((f) => f.id === 'reauthenticate' || f.id === 'mfa_reauthenticate');
	if (!needs || !handler) return first;
	const mfaTypes = first.flows?.find((f) => f.id === 'mfa_reauthenticate')?.types ?? [];
	const ok = await new Promise<boolean>((resolve) => handler!({ resolve, mfaTypes }));
	return ok ? call() : first;
}

// --- WebAuthn ------------------------------------------------------------------------------------
export const webauthnSupported = () => typeof window !== 'undefined' && !!window.PublicKeyCredential && 'parseCreationOptionsFromJSON' in PublicKeyCredential;

export async function createCredential(creationOptions: any) {
	const options = (PublicKeyCredential as any).parseCreationOptionsFromJSON(creationOptions.publicKey ?? creationOptions);
	const cred = (await navigator.credentials.create({ publicKey: options })) as PublicKeyCredential | null;
	return cred ? (cred as any).toJSON() : null;
}

export async function getCredential(requestOptions: any) {
	const options = (PublicKeyCredential as any).parseRequestOptionsFromJSON(requestOptions.publicKey ?? requestOptions);
	const cred = (await navigator.credentials.get({ publicKey: options })) as PublicKeyCredential | null;
	return cred ? (cred as any).toJSON() : null;
}

/** A cancelled or timed-out browser prompt is not an error worth shouting about. */
export const webauthnMessage = (e: unknown) =>
	e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'AbortError')
		? t('auth.err.webauthn_cancelled')
		: t('auth.err.webauthn_failed');

// --- platform API --------------------------------------------------------------------------------

export type PlatformResult<T> = { ok: boolean; status: number; data?: T; message?: string; fields: Record<string, string> };

/** A call to the platform API ({"error": {code, message, fields}} on failure) through this app's proxy. */
export async function platform<T = any>(method: string, path: string, body?: unknown, f: Fetch = fetch, headers: Record<string, string> = {}): Promise<PlatformResult<T>> {
	try {
		const res = await f(`/api/platform/v1${path}`, {
			method,
			headers: { accept: 'application/json', ...(body !== undefined ? { 'content-type': 'application/json' } : {}), ...headers },
			body: body !== undefined ? JSON.stringify(body) : undefined
		});
		if (res.status === 204) return { ok: true, status: 204, fields: {} };
		const json = await res.json().catch(() => null);
		if (res.ok) return { ok: true, status: res.status, data: json, fields: {} };
		const fields: Record<string, string> = {};
		for (const [key, messages] of Object.entries((json?.error?.fields ?? {}) as Record<string, string[]>)) fields[key] = messages[0];
		const first = Object.values(fields)[0];
		return { ok: false, status: res.status, message: json?.error?.message || first || t('auth.err.generic'), fields };
	} catch {
		return { ok: false, status: 0, message: t('auth.err.network'), fields: {} };
	}
}

/** All rows of a paginated platform endpoint (the largest page the API allows). */
export async function platformList<T>(path: string, f: Fetch = fetch): Promise<PlatformResult<T[]>> {
	const r = await platform<{ items: T[]; count: number }>('GET', `${path}${path.includes('?') ? '&' : '?'}limit=100`, undefined, f);
	return { ok: r.ok, status: r.status, message: r.message, fields: r.fields, data: r.data?.items };
}
