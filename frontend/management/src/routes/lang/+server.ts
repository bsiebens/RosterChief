import { dev } from '$app/env';
import { error } from '@sveltejs/kit';
import { LANG_COOKIE, isLang } from '#lib/i18n.svelte.ts';
import { cookieDomain, requestHostname } from '#lib/server/proxy.ts';
import type { RequestHandler } from './$types';

/** POST (form-encoded) lang=en|nl|fr: remembers the language in the rc_lang cookie. */
export const POST: RequestHandler = async (event) => {
	const { request, cookies } = event;
	const lang = (await request.formData()).get('lang');
	if (!isLang(lang)) error(400, 'Unknown language');
	cookies.set(LANG_COOKIE, lang, {
		path: '/',
		maxAge: 60 * 60 * 24 * 365,
		sameSite: 'lax',
		httpOnly: false,
		secure: !dev,
		domain: cookieDomain(requestHostname(event))
	});
	return new Response(null, { status: 204 });
};
