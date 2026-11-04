import type { RequestEvent } from '@sveltejs/kit';
import { hostFetch } from './http.ts';
import { SESSION_COOKIE, requestHostname } from './proxy.ts';
import type { Branding } from '#lib/types.ts';
import { cleanBranding } from './branding.ts';

/** Direct (server-side) call to the Django API, as the club of the browser's hostname. */
export async function backendFetch(event: RequestEvent, path: string, opts: { session?: boolean } = {}): Promise<Response> {
	const backend = (process.env.BACKEND_URL ?? 'http://localhost:8000').replace(/\/$/, '');
	const headers = new Headers({ accept: 'application/json', host: process.env.BACKEND_HOST ?? requestHostname(event) });
	const token = event.cookies.get(SESSION_COOKIE);
	if (opts.session && token) headers.set('x-session-token', token);
	return hostFetch(backend + path, { headers, signal: AbortSignal.timeout(8000) });
}

type HostInfo = { kind: 'ok'; branding: Branding } | { kind: 'not_found' } | { kind: 'unreachable' };
const cache = new Map<string, { at: number; ttl: number; info: HostInfo }>();
const TTL = 60_000;
const FAILURE_TTL = 5_000;

/** Public branding for the request's hostname, cached ~60s per hostname so a page view costs no extra round trip. */
export async function hostInfo(event: RequestEvent): Promise<HostInfo> {
	const host = requestHostname(event);
	const hit = cache.get(host);
	if (hit && Date.now() - hit.at < hit.ttl) return hit.info;

	let info: HostInfo;
	try {
		const res = await backendFetch(event, '/api/v1/club');
		if (res.status === 404) info = { kind: 'not_found' };
		else if (!res.ok) info = { kind: 'unreachable' };
		else info = { kind: 'ok', branding: cleanBranding(await res.json()) };
	} catch {
		info = { kind: 'unreachable' };
	}
	if (cache.size > 500) cache.clear(); // hostnames come from the Host header: bound the memory
	cache.set(host, { at: Date.now(), ttl: info.kind === 'ok' || info.kind === 'not_found' ? TTL : FAILURE_TTL, info });
	return info;
}

/** Forget the cached branding of this request's hostname (after the club's colours or logo changed). */
export function clearHostInfo(event: RequestEvent) {
	cache.delete(requestHostname(event));
}
