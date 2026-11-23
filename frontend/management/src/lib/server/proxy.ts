import { dev } from '$app/env';
import type { RequestEvent } from '@sveltejs/kit';
import { hostFetch } from './http.ts';

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

export const SESSION_COOKIE = 'rc_mgmt_session';
export const ALLAUTH_PREFIX = '/_allauth/app/v1/';
/** The club-scoped tenant API (members, memberships, /me, /club, /my-clubs). */
export const API_PREFIX = '/api/v1/';
/** Uploaded files (club logos), served by Django. Public, read-only: the session token is never sent along. */
export const MEDIA_PREFIX = '/media/';

const FORWARDED_REQUEST_HEADERS = [
	'content-type',
	'accept',
	'accept-language',
	'idempotency-key',
	'x-password-reset-key',
	'x-email-verification-key',
	'x-team-access',
	'user-agent',
	'if-none-match',
	'if-modified-since'
];

/** Private member pictures: Django serves them at /members/<uuid>/(player-|profile-)photo/ and checks the session token. */
const MEMBER_PHOTO = /^\/members\/[0-9a-f-]{36}\/(?:player-|profile-)?photo\/?$/;

export function isProxied(pathname: string) {
	return pathname.startsWith(ALLAUTH_PREFIX) || pathname.startsWith(API_PREFIX) || pathname.startsWith(MEDIA_PREFIX) || MEMBER_PHOTO.test(pathname);
}

const COOKIE_OPTIONS = { path: '/', httpOnly: true, sameSite: 'lax', secure: !dev, maxAge: 60 * 60 * 24 * 14 } as const;

/**
 * Cookie `Domain` for a request host. In production ROSTERCHIEF_BASE_DOMAIN (e.g. rosterchief.app) makes it
 * `.rosterchief.app`, so one sign-in covers the bare domain and every club subdomain. It stays host-only (undefined)
 * for localhost / development and for hosts outside the base domain (a browser would reject the cookie there).
 */
export function cookieDomain(hostname: string): string | undefined {
	const base = (env.ROSTERCHIEF_BASE_DOMAIN ?? '').trim().toLowerCase().replace(/^\./, '');
	if (!base || base === 'localhost' || base.endsWith('.localhost')) return undefined;
	const host = hostname.toLowerCase();
	return host === base || host.endsWith(`.${base}`) ? `.${base}` : undefined;
}

/** Header value for the session cookie. Written onto the proxy response itself: cookies set through
 * `event.cookies` are not merged into a response that a handle hook builds by hand. */
const sessionCookieHeader = (event: RequestEvent, token: string) =>
	event.cookies.serialize(SESSION_COOKIE, token, { ...COOKIE_OPTIONS, domain: cookieDomain(requestHostname(event)) });
/** Start a session on this host (used when a user is handed over from the club picker, see handoff.ts). */
export function setSessionCookie(event: RequestEvent, token: string) {
	event.cookies.set(SESSION_COOKIE, token, { ...COOKIE_OPTIONS, domain: cookieDomain(requestHostname(event)) });
}
const clearedCookieHeader = (event: RequestEvent) =>
	event.cookies.serialize(SESSION_COOKIE, '', { ...COOKIE_OPTIONS, maxAge: 0, domain: cookieDomain(requestHostname(event)) });

/** The hostname (no port) the browser used, honouring a reverse proxy's X-Forwarded-Host. */
export function requestHostname(event: RequestEvent): string {
	const forwarded = event.request.headers.get('x-forwarded-host')?.split(',')[0]?.trim();
	return (forwarded ?? event.url.host).replace(/:\d+$/, '');
}

export async function proxy(event: RequestEvent): Promise<Response> {
	const { request, url, cookies } = event;
	const backend = (env.BACKEND_URL ?? 'http://localhost:8000').replace(/\/$/, '');

	const headers = new Headers();
	for (const name of FORWARDED_REQUEST_HEADERS) {
		const value = request.headers.get(name);
		if (value) headers.set(name, value);
	}
	const isMedia = url.pathname.startsWith(MEDIA_PREFIX);
	if (isMedia && !['GET', 'HEAD'].includes(request.method)) return new Response('Method not allowed', { status: 405 });
	const token = cookies.get(SESSION_COOKIE);
	if (token && !isMedia) headers.set('x-session-token', token);
	// django-tenants picks the club from the Host header: forward the host the browser used (acme.rosterchief.app,
	// or the bare domain for the club picker). BACKEND_HOST overrides it for odd setups.
	headers.set('host', env.BACKEND_HOST ?? requestHostname(event));
	headers.set('x-forwarded-proto', url.protocol.slice(0, -1));
	try {
		headers.set('x-forwarded-for', event.getClientAddress());
	} catch {
		// no client address available (e.g. prerender)
	}

	const hasBody = !['GET', 'HEAD'].includes(request.method);
	let upstream: Response;
	try {
		upstream = await hostFetch(backend + url.pathname + url.search, {
			method: request.method,
			headers,
			body: hasBody ? await request.arrayBuffer() : undefined,
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
			setCookie.push(sessionCookieHeader(event, fresh));
			delete body.meta.session_token; // stays server-side
		}
		const loggedOut = request.method === 'DELETE' && url.pathname === `${ALLAUTH_PREFIX}auth/session`;
		if (loggedOut || upstream.status === 410) setCookie.push(clearedCookieHeader(event));
		const out = Response.json(body, { status: upstream.status });
		for (const c of setCookie) out.headers.append('set-cookie', c);
		return out;
	}

	const out = new Headers();
	for (const name of [
		'content-type', 'content-length', 'cache-control', 'etag', 'last-modified', 'www-authenticate', 'retry-after',
		'access-control-allow-origin', 'access-control-allow-methods', 'access-control-allow-headers',
		'content-security-policy', 'x-content-type-options', 'vary'
	]) {
		const value = upstream.headers.get(name);
		if (value) out.set(name, value);
	}
	return new Response(upstream.body, { status: upstream.status, headers: out });
}
