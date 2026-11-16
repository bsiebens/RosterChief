import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

/** Same-origin pass-through to the preview API (the fetch carries the session). Admin only. */
export const GET: RequestHandler = async ({ params, fetch, locals }) => {
	if (locals.auth.status !== 'authenticated' || !locals.auth.me?.can_manage_settings) error(403);
	const key = encodeURIComponent(params.key);
	const upstream = await fetch(`/api/v1/club/settings/previews/${key}/html`);
	const headers = new Headers({ 'cache-control': 'no-store' });
	const type = upstream.headers.get('content-type');
	if (type) headers.set('content-type', type);
	const subject = upstream.headers.get('x-preview-subject');
	if (subject) headers.set('x-preview-subject', subject);
	return new Response(upstream.body, { status: upstream.status, headers });
};
