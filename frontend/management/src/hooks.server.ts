import { dev } from '$app/env';
import type { Handle } from '@sveltejs/kit/hooks';
import { ALLAUTH_PREFIX, API_PREFIX, SESSION_COOKIE, isProxied, proxy, cookieDomain, requestHostname } from '#lib/server/proxy.ts';
import { backendFetch, hostInfo } from '#lib/server/backend.ts';
import { brandStyle, cleanMyClub, clubUrl, faviconTag } from '#lib/server/branding.ts';
import { LANG_COOKIE, isLang, negotiate } from '#lib/i18n.svelte.ts';
import type { AuthState, HostMode, Me, MyClub } from '#lib/types.ts';

const see = (location: string) => new Response(null, { status: 303, headers: { location } });

type Event = Parameters<Handle>[0]['event'];

async function resolveAuth(event: Event, mode: HostMode): Promise<{ auth: AuthState; myClubs: MyClub[] }> {
	const anonymous = { auth: { status: 'anonymous' } as AuthState, myClubs: [] as MyClub[] };
	if (!event.cookies.get(SESSION_COOKIE)) {
		if (dev && event.url.pathname !== '/login') console.info(`[auth] ${event.url.pathname}: no session cookie on the request`);
		return anonymous;
	}

	let res: Response;
	try {
		res = await backendFetch(event, `${ALLAUTH_PREFIX}auth/session`, { session: true });
	} catch {
		return anonymous;
	}
	const body = await res.json().catch(() => null);

	if (res.status === 401) {
		const mfa = body?.data?.flows?.find((f: any) => f.id === 'mfa_authenticate' && f.is_pending);
		return mfa ? { auth: { status: 'mfa_pending', types: mfa.types ?? [] }, myClubs: [] } : anonymous;
	}
	if (res.status !== 200 || !body?.data?.user) {
		console.warn(`[auth] session check answered ${res.status}; treating the visitor as signed out`);
		return anonymous;
	}
	const u = body.data.user;
	const user = { id: u.id, email: u.email, display: u.display };

	if (mode === 'platform') {
		// Bare domain: there is no club to be a user of. List the clubs the account belongs to instead.
		let myClubs: MyClub[] = [];
		try {
			const r = await backendFetch(event, `${API_PREFIX}my-clubs`, { session: true });
			if (r.ok) {
				const list = await r.json();
				if (Array.isArray(list)) myClubs = list.map((c) => cleanMyClub(c, clubUrl(event.url, String(c?.host ?? ''))));
			}
		} catch {
			// the picker shows "no clubs"; sign-in itself is fine
		}
		return { auth: { status: 'authenticated', user, me: null }, myClubs };
	}

	let meRes: Response;
	try {
		meRes = await backendFetch(event, `${API_PREFIX}me`, { session: true });
	} catch {
		return anonymous;
	}
	if (meRes.ok) return { auth: { status: 'authenticated', user, me: (await meRes.json()) as Me }, myClubs: [] };
	if (meRes.status === 403) {
		const err = await meRes.json().catch(() => null);
		const code = err?.code ?? err?.error?.code;
		if (code === 'no_access') {
			const portal = await backendFetch(event, `${API_PREFIX}portal/access`, { session: true }).catch(() => null);
			if (portal?.ok) {
				const info = await portal.json().catch(() => null);
				return { auth: { status: 'authenticated', user, me: null, memberShopEnabled: info?.shop_enabled === true }, myClubs: [] };
			}
		}
		return { auth: { status: code === 'mfa_required' ? 'mfa_enrollment_required' : 'no_access', user }, myClubs: [] };
	}
	console.warn(`[auth] ${API_PREFIX}me answered ${meRes.status}`);
	return anonymous;
}

export const handle: Handle = async ({ event, resolve }) => {
	if (isProxied(event.url.pathname)) return proxy(event);

	// Language: ?lang= (remembered), else the rc_lang cookie, else Accept-Language.
	const queryLang = event.url.searchParams.get('lang');
	const cookieLang = event.cookies.get(LANG_COOKIE);
	if (isLang(queryLang)) {
		event.cookies.set(LANG_COOKIE, queryLang, { path: '/', maxAge: 60 * 60 * 24 * 365, sameSite: 'lax', httpOnly: false, secure: !dev, domain: cookieDomain(requestHostname(event)) });
	}
	event.locals.lang = isLang(queryLang) ? queryLang : isLang(cookieLang) ? cookieLang : negotiate(event.request.headers.get('accept-language'));
	event.locals.auth = { status: 'anonymous' };
	event.locals.mode = 'error';
	event.locals.branding = null;
	event.locals.myClubs = [];
	event.locals.hostError = null;

	const { pathname } = event.url;
	const isStatic = pathname.startsWith('/fonts/') || pathname.startsWith('/images/') || pathname.startsWith('/_app/') || pathname === '/service-worker.js';
	let branding = null;
	let notFoundStatus = 0;

	if (!isStatic) {
		const info = await hostInfo(event);
		if (info.kind === 'ok') {
			branding = info.branding;
			event.locals.branding = info.branding;
			event.locals.mode = info.branding.is_platform ? 'platform' : 'club';
			if (pathname !== '/lang' && pathname !== '/manifest.webmanifest') {
				const r = await resolveAuth(event, event.locals.mode);
				event.locals.auth = r.auth;
				event.locals.myClubs = r.myClubs;
			}
			if (pathname === '/club-not-found') return see('/');
		} else {
			event.locals.hostError = info.kind;
			if (pathname !== '/club-not-found') return see('/club-not-found');
			notFoundStatus = info.kind === 'not_found' ? 404 : 503;
		}
	}

	const response = await resolve(event, {
		transformPageChunk: ({ html }) => html.replace('%lang%', event.locals.lang).replace('%brand%', brandStyle(branding)).replace('%favicon%', faviconTag(branding))
	});
	if (notFoundStatus) return new Response(response.body, { status: notFoundStatus, headers: response.headers });
	return response;
};
