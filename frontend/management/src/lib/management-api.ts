/**
 * Client for the club-scoped tenant API (`/api/v1`), through this app's proxy (which adds the session token and
 * forwards the club's host). Pass SvelteKit's `fetch` from load functions so the cookie and proxy apply server-side.
 *
 * Backend error shapes it understands:
 *  - `{detail, code, errors?: {field: [messages]}}`   (our 403/404/422 handlers; `code` e.g. "invalid", "forbidden", "mfa_required")
 *  - `{detail: [{loc, msg}]}`                          (ninja request validation, 422)
 */
export type Fetch = typeof fetch;

export type ApiCall<T> = {
	ok: boolean;
	status: number;
	data?: T;
	/** Human-readable summary of the failure. */
	message?: string;
	/** The backend's machine code, when it sent one. */
	code?: string;
	/** Field errors keyed by field name (422). */
	fields: Record<string, string>;
};

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
	if (!query) return path;
	const params = new URLSearchParams();
	for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') params.set(k, String(v));
	const qs = params.toString();
	return qs ? `${path}${path.includes('?') ? '&' : '?'}${qs}` : path;
}

export async function api<T = any>(method: string, path: string, opts: { body?: unknown; form?: FormData; query?: Query; f?: Fetch } = {}): Promise<ApiCall<T>> {
	const { body, form, query, f = fetch } = opts;
	try {
		const res = await f(withQuery(`/api/v1${path}`, query), {
			method,
			headers: { accept: 'application/json', ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
			body: form ?? (body !== undefined ? JSON.stringify(body) : undefined)
		});
		if (res.status === 204) return { ok: true, status: 204, fields: {} };
		const json = await res.json().catch(() => undefined);
		if (res.ok) return { ok: true, status: res.status, data: json as T, fields: {} };
		return { ok: false, status: res.status, ...parseError(json, res.status) };
	} catch {
		return { ok: false, status: 0, code: 'network', message: 'network', fields: {} };
	}
}

function parseError(json: any, status: number): { message?: string; code?: string; fields: Record<string, string> } {
	const fields: Record<string, string> = {};
	if (Array.isArray(json?.detail)) {
		for (const e of json.detail) {
			const location = Array.isArray(e.loc) ? e.loc : [];
			const name = location.at(-1);
			if (name !== undefined && name !== 'body') fields[String(name)] ??= e.msg;
			if (location[0] === 'body' && location[1] === 'payload' && location.length > 3) {
				fields[location.slice(2).join('.')] = e.msg;
			}
		}
		return { message: json.detail[0]?.msg, code: 'invalid', fields };
	}
	if (json?.errors && typeof json.errors === 'object') {
		for (const [k, v] of Object.entries(json.errors)) fields[k] = Array.isArray(v) ? String(v[0]) : String(v);
	}
	return { message: typeof json?.detail === 'string' ? json.detail : json?.error?.message, code: json?.code ?? json?.error?.code ?? (status === 401 ? 'unauthenticated' : undefined), fields };
}

/** Paginated list envelope used by the list endpoints. */
export type Page<T> = { items: T[]; count: number };

export async function apiPage<T>(path: string, query: Query = {}, f: Fetch = fetch): Promise<ApiCall<Page<T>>> {
	return api<Page<T>>('GET', path, { query, f });
}
