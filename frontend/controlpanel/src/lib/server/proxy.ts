import { dev } from '$app/env';
import type { Cookies, RequestEvent } from '@sveltejs/kit';

/**
 * The browser never talks to Django directly. This app forwards two prefixes and keeps the allauth
 * session token in an httpOnly cookie, so the token is never readable by page scripts and the backend
 * needs no CORS configuration.
 */
// Server-only runtime config. `.env` is read here because Kit 3 no longer injects it into process.env in dev.
try {
	process.loadEnvFile('.env');
} catch {
	// no .env file: use the real environment
}
const env = process.env;

export const SESSION_COOKIE = 'rc_cp_session';
export const ALLAUTH_PREFIX = '/_allauth/app/v1/';
export const PLATFORM_PREFIX = '/api/platform/v1/';

const FORWARDED_REQUEST_HEADERS = [
	'content-type',
	'accept',
	'accept-language',
	'idempotency-key',
	'x-password-reset-key',
	'x-email-verification-key',
	'user-agent'
];

export function isProxied(pathname: string) {
	return pathname.startsWith(ALLAUTH_PREFIX) || pathname.startsWith(PLATFORM_PREFIX);
}

const COOKIE_OPTIONS = { path: '/', httpOnly: true, sameSite: 'lax', secure: !dev, maxAge: 60 * 60 * 24 * 14 } as const;

/** Header value for the session cookie. Written onto the proxy response itself: cookies set through
 * `event.cookies` are not merged into a response that a handle hook builds by hand. */
const sessionCookieHeader = (cookies: Cookies, token: string) => cookies.serialize(SESSION_COOKIE, token, COOKIE_OPTIONS);
const clearedCookieHeader = (cookies: Cookies) => cookies.serialize(SESSION_COOKIE, '', { ...COOKIE_OPTIONS, maxAge: 0 });

export async function proxy(event: RequestEvent): Promise<Response> {
	const { request, url, cookies } = event;
	const backend = (env.BACKEND_URL ?? 'http://localhost:8000').replace(/\/$/, '');

	const headers = new Headers();
	for (const name of FORWARDED_REQUEST_HEADERS) {
		const value = request.headers.get(name);
		if (value) headers.set(name, value);
	}
	const token = cookies.get(SESSION_COOKIE);
	if (token) headers.set('x-session-token', token);
	if (env.BACKEND_HOST) headers.set('host', env.BACKEND_HOST);
	try {
		headers.set('x-forwarded-for', event.getClientAddress());
	} catch {
		// no client address available (e.g. prerender)
	}

	const hasBody = !['GET', 'HEAD'].includes(request.method);
	let upstream: Response;
	try {
		upstream = await fetch(backend + url.pathname + url.search, {
			method: request.method,
			headers,
			body: hasBody ? await request.arrayBuffer() : undefined,
			redirect: 'manual'
		});
	} catch {
		return Response.json(
			{ error: { code: 'backend_unreachable', message: 'The RosterChief backend could not be reached.' } },
			{ status: 502 }
		);
	}

	const isAuthApi = url.pathname.startsWith(ALLAUTH_PREFIX);
	const contentType = upstream.headers.get('content-type') ?? '';

	if (isAuthApi && contentType.includes('application/json')) {
		const body = await upstream.json();
		const fresh = body?.meta?.session_token;
		const setCookie: string[] = [];
		if (dev && url.pathname.includes('/auth/') && request.method === 'POST') {
			console.info(`[proxy] ${url.pathname} -> ${upstream.status}, session token ${typeof fresh === 'string' ? 'received' : 'MISSING'}, cookie secure=${!dev}`);
		}
		if (typeof fresh === 'string') {
			setCookie.push(sessionCookieHeader(cookies, fresh));
			delete body.meta.session_token; // stays server-side
		}
		const loggedOut = request.method === 'DELETE' && url.pathname === `${ALLAUTH_PREFIX}auth/session`;
		if (loggedOut || upstream.status === 410) setCookie.push(clearedCookieHeader(cookies));
		const out = Response.json(body, { status: upstream.status });
		for (const c of setCookie) out.headers.append('set-cookie', c);
		return out;
	}

	const out = new Headers();
	for (const name of ['content-type', 'www-authenticate', 'retry-after']) {
		const value = upstream.headers.get(name);
		if (value) out.set(name, value);
	}
	return new Response(upstream.body, { status: upstream.status, headers: out });
}
