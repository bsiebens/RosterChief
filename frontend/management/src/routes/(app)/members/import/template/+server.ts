import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

/** Same-origin pass-through to the CSV template (the fetch carries the session). Needs members.manage_members. */
export const GET: RequestHandler = async ({ fetch, locals }) => {
	if (locals.auth.status !== 'authenticated' || !locals.auth.me?.can_manage_members) error(403);
	const upstream = await fetch('/api/v1/members/import/template');
	const headers = new Headers({ 'cache-control': 'no-store' });
	for (const h of ['content-type', 'content-disposition']) {
		const v = upstream.headers.get(h);
		if (v) headers.set(h, v);
	}
	return new Response(upstream.body, { status: upstream.status, headers });
};
