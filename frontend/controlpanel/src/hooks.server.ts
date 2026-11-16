import { dev } from '$app/env';
import type { Handle } from '@sveltejs/kit/hooks';
import { PLATFORM_PREFIX, ALLAUTH_PREFIX, SESSION_COOKIE, isProxied, proxy } from '#lib/server/proxy.ts';
import type { AuthState } from '#lib/types.ts';

async function resolveAuth(event: Parameters<Handle>[0]['event']): Promise<AuthState> {
	if (!event.cookies.get(SESSION_COOKIE)) {
		if (dev && event.url.pathname !== '/login') console.info(`[auth] ${event.url.pathname}: no session cookie on the request`);
		return { status: 'anonymous' };
	}

	const res = await event.fetch(`${ALLAUTH_PREFIX}auth/session`);
	const body = await res.json().catch(() => null);

	if (res.status === 401) {
		const mfa = body?.data?.flows?.find((f: any) => f.id === 'mfa_authenticate' && f.is_pending);
		return mfa ? { status: 'mfa_pending', types: mfa.types ?? [] } : { status: 'anonymous' };
	}
	if (res.status !== 200 || !body?.data?.user) {
		console.warn(`[auth] session check answered ${res.status}; treating the visitor as signed out`);
		return { status: 'anonymous' };
	}

	const u = body.data.user;
	const user = { id: u.id, email: u.email, display: u.display };

	const meRes = await event.fetch(`${PLATFORM_PREFIX}me`);
	if (meRes.ok) return { status: 'authenticated', user, me: await meRes.json() };
	if (meRes.status === 403) {
		const err = await meRes.json().catch(() => null);
		return err?.error?.code === 'mfa_enrollment_required'
			? { status: 'mfa_enrollment_required', user }
			: { status: 'no_access', user };
	}
	console.warn(`[auth] ${PLATFORM_PREFIX}me answered ${meRes.status}: ${(await meRes.text().catch(() => '')).slice(0, 200)}`);
	return { status: 'anonymous' };
}

export const handle: Handle = async ({ event, resolve }) => {
	if (isProxied(event.url.pathname)) return proxy(event);

	// Static assets and framework internals never need the session.
	const { pathname } = event.url;
	if (pathname.startsWith('/fonts/') || pathname.startsWith('/images/') || pathname.startsWith('/_app/')) {
		event.locals.auth = { status: 'anonymous' };
	} else {
		event.locals.auth = await resolveAuth(event);
	}
	return resolve(event);
};
