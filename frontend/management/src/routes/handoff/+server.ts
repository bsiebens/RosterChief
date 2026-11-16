import { redirect } from '@sveltejs/kit';
import { requestHostname, setSessionCookie } from '#lib/server/proxy.ts';
import { redeemHandoff } from '#lib/server/handoff.ts';
import type { RequestHandler } from './$types';

/** Club host: redeem the one-time code from the club picker and start the session here. */
export const GET: RequestHandler = (event) => {
	const code = event.url.searchParams.get('code') ?? '';
	const token = code ? redeemHandoff(code, requestHostname(event)) : null;
	if (!token) redirect(303, '/login');
	setSessionCookie(event, token);
	redirect(303, '/');
};
